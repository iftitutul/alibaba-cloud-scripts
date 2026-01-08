#!/usr/bin/env python3
# Alibaba Cloud ECS Inventory (Riyadh only / me-central-1)
# Output: CSV + HTML (white background) with global search + per-column filters (FIXED)
# Includes: CPU/Mem/Disk, SG, ENI, OS Name, ImageId, Snapshots
# Read-only APIs only.

import html
import time
import os
import configparser
from pathlib import Path
from datetime import datetime

import pandas as pd

from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models

REGION_ID = "me-central-1"  # Riyadh

# Low impact tuning
ECS_PAGE_SIZE = 20
ECS_SLEEP_BETWEEN_PAGES = 0.2
INSTANCE_ATTR_SLEEP = 0.06
DISK_SLEEP_PER_INSTANCE = 0.12
SNAPSHOT_SLEEP_PER_INSTANCE = 0.12
INCLUDE_SNAPSHOTS = True
PROFILE_NAME="masdr-env"
# If DescribeInstanceAttribute is blocked by RAM policy, you'll see one warning.
DEBUG_PRINT_ATTR_ERRORS = True
_printed_attr_error = False

STATE_COLOR = {
    "Running": "#2e7d32",
    "Stopped": "#c62828",
    "Starting": "#1565c0",
    "Stopping": "#ef6c00",
    "Pending": "#1565c0",
    "Deleted": "#616161",
}

HEADERS = [
    "Profile",
    "Name",
    "InstanceId",
    "Region",
    "InstanceType",
    "vCPU",
    "Memory(GB)",
    "OS Name",
    "ImageId",
    "ENI IDs",
    "Public IPs",
    "Private IPs",
    "Security Groups",
    "DiskTotal(GB)",
    "SystemDisk(GB)",
    "DataDisks(GB)",
    "DiskCategories",
    "Disk IDs",
    "SnapshotCount",
    "LatestSnapshotTime",
    "State",
    "AZ",
    "VPC",
    "VSwitch",
    "Key Pair",
    "Creation Time",
]

def ecs_client_default() -> EcsClient:
    cred = CredentialClient()
    cfg = open_api_models.Config(region_id=REGION_ID, credential=cred)
    return EcsClient(cfg)

def _safe_join(vals):
    vals = [str(v) for v in (vals or []) if v]
    out, seen = [], set()
    for v in vals:
        if v not in seen:
            out.append(v)
            seen.add(v)
    return "; ".join(out)


def _mb_to_gb(mb):
    try:
        if mb is None or mb == "":
            return ""
        return round(float(mb) / 1024.0, 2)
    except Exception:
        return ""


def _badge(state: str) -> str:
    s = state or ""
    c = STATE_COLOR.get(s, "#424242")
    return f"<span style='background:{c};color:#fff;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700'>{html.escape(s)}</span>"


def _ecs_link(instance_id: str) -> str:
    iid = html.escape(instance_id or "")
    url = f"https://ecs.console.aliyun.com/#/server/{REGION_ID}/{iid}/detail"
    # data-value lets JS filters/search read a clean string
    return f"<a href='{url}' data-value='{iid}' target='_blank' rel='noopener noreferrer' style='font-weight:700'>{iid}</a>"


def get_public_ips(ins):
    ips = []
    pip = getattr(ins, "public_ip_address", None)
    if pip and getattr(pip, "ip_address", None):
        ips.extend([str(x) for x in pip.ip_address if x])
    return ips


def get_network_interfaces(ins):
    enis = []
    pri_ips = []
    nis = getattr(ins, "network_interfaces", None)
    if nis and getattr(nis, "network_interface", None):
        for ni in nis.network_interface:
            eni_id = getattr(ni, "network_interface_id", None)
            if eni_id:
                enis.append(str(eni_id))
            ip = getattr(ni, "primary_ip_address", None)
            if ip:
                pri_ips.append(str(ip))
    return enis, pri_ips


def get_private_ips(ins):
    ips = []
    _, eni_ips = get_network_interfaces(ins)
    ips.extend(eni_ips)

    vpc = getattr(ins, "vpc_attributes", None)
    if vpc:
        pip = getattr(vpc, "private_ip_address", None)
        if pip and getattr(pip, "ip_address", None):
            ips.extend([str(x) for x in pip.ip_address if x])

    iip = getattr(ins, "inner_ip_address", None)
    if iip and getattr(iip, "ip_address", None):
        ips.extend([str(x) for x in iip.ip_address if x])

    return list(dict.fromkeys([x for x in ips if x]))


def get_security_groups(ins):
    sg = getattr(ins, "security_group_ids", None)
    if sg and getattr(sg, "security_group_id", None):
        return [str(x) for x in sg.security_group_id if x]
    return []


def _body_to_map_safe(body):
    try:
        m = body.to_map()
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def _pick_from_map(m: dict, keys):
    for k in keys:
        cur = m
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return str(cur)
    return ""


def get_os_image_best_effort(ecs: EcsClient, ins):
    """
    OS Name + ImageId can be blank in DescribeInstances.
    Strategy:
      1) Try from DescribeInstances
      2) If blank -> DescribeInstanceAttribute + robust extraction
    """
    global _printed_attr_error

    os_name = (
        getattr(ins, "os_name_en", None)
        or getattr(ins, "os_name", None)
        or getattr(ins, "osname", None)
        or ""
    )
    image_id = (
        getattr(ins, "image_id", None)
        or getattr(ins, "imageid", None)
        or ""
    )

    if os_name and image_id:
        return str(os_name), str(image_id)

    iid = getattr(ins, "instance_id", "") or ""
    if not iid:
        return str(os_name or ""), str(image_id or "")

    try:
        req = ecs_models.DescribeInstanceAttributeRequest(region_id=REGION_ID, instance_id=iid)
        body = ecs.describe_instance_attribute(req).body

        os2 = (
            getattr(body, "os_name_en", None)
            or getattr(body, "os_name", None)
            or getattr(body, "osname", None)
            or ""
        )
        img2 = (
            getattr(body, "image_id", None)
            or getattr(body, "imageid", None)
            or ""
        )

        m = _body_to_map_safe(body)
        if not os2:
            os2 = _pick_from_map(m, [
                "OSNameEn", "OSName", "OsNameEn", "OsName",
                "InstanceAttribute.OSNameEn", "InstanceAttribute.OSName",
                "Body.OSNameEn", "Body.OSName",
            ])
        if not img2:
            img2 = _pick_from_map(m, [
                "ImageId", "imageId", "image_id",
                "InstanceAttribute.ImageId",
                "Body.ImageId",
            ])

        os_name = str(os_name or os2 or "")
        image_id = str(image_id or img2 or "")

    except Exception as e:
        if DEBUG_PRINT_ATTR_ERRORS and not _printed_attr_error:
            _printed_attr_error = True
            print(f"[WARN] DescribeInstanceAttribute failed (likely RAM permission). Example error: {e}")

    if INSTANCE_ATTR_SLEEP:
        time.sleep(INSTANCE_ATTR_SLEEP)

    return str(os_name or ""), str(image_id or "")


def list_disks_for_instance(ecs: EcsClient, instance_id: str):
    disks = []
    page = 1
    page_size = 50
    while True:
        req = ecs_models.DescribeDisksRequest(
            region_id=REGION_ID,
            instance_id=instance_id,
            page_number=page,
            page_size=page_size,
        )
        body = ecs.describe_disks(req).body
        dlist = body.disks.disk if (body and body.disks and body.disks.disk) else []
        disks.extend(dlist)

        total = int(getattr(body, "total_count", 0) or 0)
        if page * page_size >= total:
            break
        page += 1
    return disks


def disk_summary(disks):
    total = system = data = 0
    cats = set()
    disk_ids = []
    for d in disks:
        size = int(getattr(d, "size", 0) or 0)  # GB
        total += size
        dt = (getattr(d, "type", "") or "").lower()
        if dt == "system":
            system += size
        elif dt == "data":
            data += size

        cat = getattr(d, "category", "") or ""
        if cat:
            cats.add(cat)

        did = getattr(d, "disk_id", "") or ""
        if did:
            disk_ids.append(did)

    return total, system, data, ", ".join(sorted(cats)), disk_ids


def snapshot_summary_for_instance(ecs: EcsClient, instance_id: str):
    if not INCLUDE_SNAPSHOTS:
        return "", ""

    latest = None
    count = 0
    page = 1
    page_size = 50
    while True:
        req = ecs_models.DescribeSnapshotsRequest(
            region_id=REGION_ID,
            instance_id=instance_id,
            page_number=page,
            page_size=page_size,
        )
        body = ecs.describe_snapshots(req).body
        snaps = body.snapshots.snapshot if (body and body.snapshots and body.snapshots.snapshot) else []
        for s in snaps:
            count += 1
            ct = getattr(s, "creation_time", None)
            if ct and (latest is None or ct > latest):
                latest = ct

        total = int(getattr(body, "total_count", 0) or 0)
        if page * page_size >= total:
            break
        page += 1

    return str(count), (str(latest) if latest else "")


# ---------------------------
# HTML export (CORRECTED)
# ---------------------------
def write_html_table(df: pd.DataFrame, html_path: str, title: str):
    cols = list(df.columns)

    rows_html = []
    for _, r in df.iterrows():
        rows_html.append("<tr>" + "".join(f"<td>{r[c]}</td>" for c in cols) + "</tr>")

    th_html = "".join(f"<th>{html.escape(c)}</th>" for c in cols)

    # Use special tokens to avoid conflict between "All" and "(blank)"
    filter_row = "".join(
        f"<th><select class='f' data-col='{i}'><option value='__ALL__'>All</option></select></th>"
        for i in range(len(cols))
    )

    page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body{{font-family:Arial, sans-serif;margin:0;background:#ffffff;color:#111827}}
  .wrap{{padding:18px}}
  .title{{font-size:30px;font-weight:900;margin:0 0 10px;color:#0b5ed7}}
  .meta{{color:#4b5563;font-size:13px;margin-bottom:14px}}
  .controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:10px 0 14px}}
  #q{{padding:10px 12px;border:1px solid #d1d5db;border-radius:10px;background:#ffffff;color:#111827;width:420px;max-width:100%}}
  table{{border-collapse:collapse;width:100%;font-size:12.5px;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden}}
  th,td{{border-bottom:1px solid #e5e7eb;padding:8px;vertical-align:top}}
  th{{position:sticky;top:0;background:#f3f4f6;text-align:left;z-index:3}}
  thead tr.filters th{{top:39px;background:#ffffff;z-index:2}}
  tr:nth-child(even){{background:#fcfcfd}}
  select.f{{width:100%;padding:6px;border-radius:10px;border:1px solid #d1d5db;background:#ffffff;color:#111827}}
  a{{color:#0b5ed7;text-decoration:none}}
  a:hover{{text-decoration:underline}}
</style>
</head>
<body>
  <div class="wrap">
    <h1 class="title">{html.escape(title)}</h1>
    <div class="meta"><b>Tenant:</b> {html.escape(PROFILE_NAME)} &nbsp; | Region:</b> {html.escape(REGION_ID)} &nbsp; | &nbsp; <b>Rows:</b> {len(df)}</div>

    <div class="controls">
      <input id="q" placeholder="Search everything..." />
    </div>

    <table id="t">
      <thead>
        <tr>{th_html}</tr>
        <tr class="filters">{filter_row}</tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>

<script>
(function(){{
  const ALL = "__ALL__";
  const BLANK = "__BLANK__";

  function cellText(cell) {{
    if (!cell) return "";
    // If cell contains an <a data-value="..."> use that for stable filtering
    const a = cell.querySelector && cell.querySelector("a[data-value]");
    if (a && a.getAttribute("data-value") !== null) {{
      return (a.getAttribute("data-value") || "").trim();
    }}
    return (cell.textContent || "").trim();
  }}

  function populateFilters() {{
    const t = document.getElementById("t");
    const tbody = t.tBodies && t.tBodies.length ? t.tBodies[0] : null;
    if (!tbody) return;

    const rows = Array.from(tbody.rows);
    const selects = Array.from(document.querySelectorAll("select.f"));
    const MAX_UNIQUE = 500;

    selects.forEach(sel => {{
      const col = parseInt(sel.getAttribute("data-col"), 10);
      const values = new Set();

      rows.forEach(r => values.add(cellText(r.cells[col])));

      const prev = sel.value || ALL;

      // rebuild options
      sel.innerHTML = "";
      const oAll = document.createElement("option");
      oAll.value = ALL;
      oAll.textContent = "All";
      sel.appendChild(oAll);

      const vals = Array.from(values).slice(0, MAX_UNIQUE).sort((a,b)=>a.localeCompare(b));
      vals.forEach(v => {{
        const opt = document.createElement("option");
        if (v === "") {{
          opt.value = BLANK;
          opt.textContent = "(blank)";
        }} else {{
          opt.value = v;
          opt.textContent = v.length > 140 ? v.slice(0,140) + "…" : v;
        }}
        sel.appendChild(opt);
      }});

      // restore selection if still exists
      sel.value = prev;
      if (![...sel.options].some(o => o.value === sel.value)) sel.value = ALL;

      sel.addEventListener("change", applyFilters);
    }});
  }}

  function applyFilters() {{
    const t = document.getElementById("t");
    const rows = Array.from(t.tBodies[0].rows);
    const selects = Array.from(document.querySelectorAll("select.f"));
    const q = (document.getElementById("q").value || "").toLowerCase();

    rows.forEach(r => {{
      // global search (textContent includes link text too)
      const rowText = (r.textContent || "").toLowerCase();
      if (q && !rowText.includes(q)) {{
        r.style.display = "none";
        return;
      }}

      // per-column exact filters
      for (let i = 0; i < selects.length; i++) {{
        const want = selects[i].value || ALL;
        if (want === ALL) continue;

        const got = cellText(r.cells[i]);

        if (want === BLANK) {{
          if (got !== "") {{
            r.style.display = "none";
            return;
          }}
        }} else {{
          if (got !== want) {{
            r.style.display = "none";
            return;
          }}
        }}
      }}

      r.style.display = "";
    }});
  }}

  document.addEventListener("DOMContentLoaded", function() {{
    document.getElementById("q").addEventListener("input", applyFilters);
    populateFilters();
    applyFilters();
  }});

  // expose if needed
  window.applyFilters = applyFilters;
}})();
</script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    start = time.time()
    ecs = ecs_client_default()

    rows_csv = []
    rows_html = []

    page = 1
    while True:
        req = ecs_models.DescribeInstancesRequest(region_id=REGION_ID, page_number=page, page_size=ECS_PAGE_SIZE)
        body = ecs.describe_instances(req).body
        insts = body.instances.instance if (body and body.instances and body.instances.instance) else []
        if not insts:
            break

        for ins in insts:
            iid = getattr(ins, "instance_id", "") or ""
            name = getattr(ins, "instance_name", "") or ""
            itype = getattr(ins, "instance_type", "") or ""
            state = getattr(ins, "status", "") or ""
            az = getattr(ins, "zone_id", "") or ""
            ctime = getattr(ins, "creation_time", "") or ""

            vcpu = getattr(ins, "cpu", "") or ""
            mem_gb = _mb_to_gb(getattr(ins, "memory", "") or "")

            vpc_id = ""
            vsw_id = ""
            vpc = getattr(ins, "vpc_attributes", None)
            if vpc:
                vpc_id = getattr(vpc, "vpc_id", "") or ""
                vsw_id = getattr(vpc, "v_switch_id", "") or ""

            keyp = getattr(ins, "key_pair_name", "") or ""

            pub_ips = get_public_ips(ins)
            enis, _ = get_network_interfaces(ins)
            pri_ips = get_private_ips(ins)
            sgs = get_security_groups(ins)

            os_name, image_id = get_os_image_best_effort(ecs, ins)

            # disks
            try:
                disks = list_disks_for_instance(ecs, iid)
                d_total, d_sys, d_data, d_cat, d_ids = disk_summary(disks)
            except Exception:
                d_total, d_sys, d_data, d_cat, d_ids = 0, 0, 0, "", []
            time.sleep(DISK_SLEEP_PER_INSTANCE)

            # snapshots
            snap_count, snap_latest = "", ""
            if INCLUDE_SNAPSHOTS and iid:
                try:
                    snap_count, snap_latest = snapshot_summary_for_instance(ecs, iid)
                except Exception:
                    snap_count, snap_latest = "", ""
                time.sleep(SNAPSHOT_SLEEP_PER_INSTANCE)

            # CSV row
            rows_csv.append([
                PROFILE_NAME, name, iid, REGION_ID,
                itype, vcpu, mem_gb,
                os_name, image_id,
                _safe_join(enis), _safe_join(pub_ips), _safe_join(pri_ips),
                _safe_join(sgs),
                d_total, d_sys, d_data, d_cat, _safe_join(d_ids),
                snap_count, snap_latest,
                state, az, vpc_id, vsw_id, keyp, ctime,
            ])

            # HTML row (InstanceId as link, State as badge)
            rows_html.append([
                html.escape(PROFILE_NAME),
                html.escape(name),
                _ecs_link(iid),
                html.escape(REGION_ID),
                html.escape(str(itype)),
                html.escape(str(vcpu)),
                html.escape(str(mem_gb)),
                html.escape(str(os_name)),
                html.escape(str(image_id)),
                html.escape(_safe_join(enis)),
                html.escape(_safe_join(pub_ips)),
                html.escape(_safe_join(pri_ips)),
                html.escape(_safe_join(sgs)),
                html.escape(str(d_total)),
                html.escape(str(d_sys)),
                html.escape(str(d_data)),
                html.escape(str(d_cat)),
                html.escape(_safe_join(d_ids)),
                html.escape(str(snap_count)),
                html.escape(str(snap_latest)),
                _badge(state),
                html.escape(str(az)),
                html.escape(str(vpc_id)),
                html.escape(str(vsw_id)),
                html.escape(str(keyp)),
                html.escape(str(ctime)),
            ])

        total = int(getattr(body, "total_count", 0) or 0)
        if page * ECS_PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(ECS_SLEEP_BETWEEN_PAGES)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    base = f"alibaba-ecs-riyadh-({PROFILE_NAME})-{ts}"
    csv_path = base + ".csv"
    html_path = base + ".html"
    title = f"Alibaba ECS Inventory (Tenant: {PROFILE_NAME}) - Generated on {datetime.now().strftime('%d-%b-%Y %H:%M')}"

    df_csv = pd.DataFrame(rows_csv, columns=HEADERS)
    df_csv.to_csv(csv_path, index=False)

    df_html = pd.DataFrame(rows_html, columns=HEADERS)
    write_html_table(df_html, html_path, title)

    print(f"\nCSV:  ./{csv_path} (rows: {len(df_csv)})")
    print(f"HTML: ./{html_path}")
    print("Time Required:", round(time.time() - start, 2), "seconds")


if __name__ == "__main__":
    main()
