#!/usr/bin/env python3
import json
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import oss2

import socket
import urllib.request


# ----------------------------
# 1) Credential Loader
# ----------------------------
def get_creds_from_json(profile_name):
    config_path = Path.home() / ".aliyun" / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at {config_path}")
    with open(config_path, "r") as f:
        config_data = json.load(f)
    target = next((p for p in config_data.get("profiles", []) if p.get("name") == profile_name), None)
    if not target:
        raise ValueError(f"Profile [{profile_name}] not found.")
    return target.get("access_key_id"), target.get("access_key_secret")


# ----------------------------
# 2) Formatting Helpers
# ----------------------------
def format_bytes(b):
    try:
        b = float(b)
        if b <= 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024:
                return f"{b:.2f} {unit}"
            b /= 1024
        return f"{b:.2f} PB"
    except Exception:
        return "0 B"


def safe_date(ts):
    if not ts:
        return "N/A"
    try:
        if isinstance(ts, str) and "T" in ts:
            return ts.split("T")[0]
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


def _badge(val, type="acl"):
    val = str(val or "Unknown").lower()
    bg = "#64748b"
    if type == "acl":
        bg = {"private": "#16a34a", "public-read": "#ea580c", "public-read-write": "#dc2626"}.get(val, bg)
    elif type == "redundancy":
        bg = {"zrs": "#2563eb", "lrs": "#475569"}.get(val, bg)
    return (
        "<span class='badge' "
        f"style='background:{bg};color:white;padding:3px 8px;border-radius:4px;font-weight:bold;font-size:10px;'>"
        f"{val.upper()}</span>"
    )


def ensure_https(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith("https://"):
        return endpoint
    if endpoint.startswith("http://"):
        return "https://" + endpoint[len("http://") :]
    return "https://" + endpoint


# ----------------------------
# 3) OSS Security/Logging Helpers
# ----------------------------
def get_access_logging_status(bucket_client):
    enabled = "No"
    target = "N/A"
    try:
        cfg = bucket_client.get_bucket_logging()

        tb = getattr(cfg, "target_bucket", None)
        tp = getattr(cfg, "target_prefix", None)

        if not tb and hasattr(cfg, "logging"):
            tb = getattr(cfg.logging, "target_bucket", None)
            tp = getattr(cfg.logging, "target_prefix", None)

        if tb:
            enabled = "Yes"
            target = f"{tb}/{tp or ''}".rstrip("/")
    except Exception:
        pass
    return enabled, target


def http_public_probe(bucket_name: str, region: str) -> str:
    socket.setdefaulttimeout(3)
    url = f"http://{bucket_name}.oss-{region}.aliyuncs.com/"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as resp:
            return f"Reachable ({resp.status})"
    except Exception:
        return "Not Reachable"


# ----------------------------
# 4) Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="me-central-1")
    parser.add_argument("--skip-http-probe", action="store_true", help="Skip HTTP reachability probe (faster).")
    args = parser.parse_args()

    tenant = args.profile
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_ts = datetime.now().strftime("%Y%m%d-%H%M")

    try:
        ak, sk = get_creds_from_json(tenant)
        auth = oss2.Auth(ak, sk)
        service = oss2.Service(auth, f"https://oss-{args.region}.aliyuncs.com")
        buckets = service.list_buckets().buckets
    except Exception as e:
        print(f"❌ Error: {e}")
        return

    data_rows = []
    total_bytes, total_objects = 0, 0

    print(f"🚀 Generating Masdr OSS Dashboard for {tenant}...")

    for b in buckets:
        try:
            endpoint_https = ensure_https(getattr(b, "extranet_endpoint", ""))
            b_client = oss2.Bucket(auth, endpoint_https, b.name)

            info = b_client.get_bucket_info()
            stat = b_client.get_bucket_stat()

            try:
                versioning = b_client.get_bucket_versioning().status or "Off"
            except Exception:
                versioning = "Off"

            try:
                accel = "Enabled" if b_client.get_bucket_transfer_acceleration().enabled else "Disabled"
            except Exception:
                accel = "Disabled"

            access_logging, log_target = get_access_logging_status(b_client)

            http_probe = "Skipped" if args.skip_http_probe else http_public_probe(b.name, args.region)

            total_bytes += stat.storage_size_in_bytes
            total_objects += stat.object_count

            data_rows.append(
                {
                    "Bucket Name": b.name,
                    "Region": b.location.replace("oss-", ""),
                    "Storage Class": getattr(info, "storage_class", "Standard"),
                    "Capacity": format_bytes(stat.storage_size_in_bytes),
                    "Objects": stat.object_count,
                    "ACL": getattr(info.acl, "grant", "private"),
                    "Redundancy": getattr(info, "data_redundancy_type", "LRS"),
                    "Versioning": versioning,
                    "Acceleration": accel,
                    "Created At": safe_date(getattr(info, "creation_date", None)),
                    "Client TLS": "Yes (HTTPS)",
                    "Access Logging": access_logging,
                    "Log Target": log_target,
                    "HTTP Public Probe": http_probe,
                }
            )

            print(f"✅ Loaded: {b.name}")
        except Exception:
            continue

    if not data_rows:
        print("⚠️ No buckets found or no access.")
        return

    df = pd.DataFrame(data_rows)
    headers = "".join([f"<th>{col}</th>" for col in df.columns])

    rows_html = ""
    for _, r in df.iterrows():
        rows_html += "<tr>"
        for col, val in r.items():
            disp_val = (
                _badge(val, "acl")
                if col == "ACL"
                else (_badge(val, "redundancy") if col == "Redundancy" else val)
            )
            rows_html += f"<td>{disp_val}</td>"
        rows_html += "</tr>"

    select_filter_cols = [
        "Region",
        "Storage Class",
        "ACL",
        "Redundancy",
        "Versioning",
        "Acceleration",
        "Client TLS",
        "Access Logging",
        "HTTP Public Probe",
    ]

    html_out = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Masdr OSS Inventory - {tenant}</title>
  <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
  <style>
    body {{ font-family: 'Inter', sans-serif; background: #f0f4f8; padding: 40px; color: #334155; }}
    .main-header {{ margin-bottom: 25px; }}
    .main-header h1 {{ margin: 0; font-size: 26px; color: #1e293b; }}
    .meta-info {{ color: #64748b; font-size: 13px; margin-top: 5px; font-weight: 500; }}
    .stats-bar {{ display: flex; gap: 20px; margin-bottom: 30px; flex-wrap: wrap; }}
    .card {{ background: white; padding: 20px; border-radius: 12px; flex: 1; min-width: 220px;
             box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border-top: 4px solid #3b82f6; }}
    .card-label {{ font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card-val {{ font-size: 24px; font-weight: 800; display: block; margin-top: 5px; color: #1e293b; }}

    .table-container {{
      background: white;
      padding: 20px;
      border-radius: 12px;
      box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
      overflow-x: auto;
    }}

    table {{ font-size: 13px; width: 100% !important; }}
    thead th {{ background: #f8fafc; color: #475569; font-weight: 700;
               border-bottom: 2px solid #e2e8f0 !important; white-space: nowrap; }}
    .filter-row th {{ background: #ffffff !important; padding: 10px !important; }}

    select, input {{
      width: 100%;
      padding: 6px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font-size: 11px;
      background: #fff;
      box-sizing: border-box;
    }}

    /* ✅ FIX: remove ugly outline "block" and use clean focus ring */
    input:focus, select:focus {{
      outline: none !important;
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59,130,246,0.18);
    }}

    .badge {{ min-width: 70px; display: inline-block; text-align: center; }}
  </style>
</head>
<body>
  <div class="main-header">
    <h1>Masdr OSS Inventory</h1>
    <div class="meta-info">Tenant: <b>{tenant}</b> | Generated: <b>{gen_time}</b></div>
  </div>

  <div class="stats-bar">
    <div class="card"><span class="card-label">Total Buckets</span><span id="statCount" class="card-val">{len(data_rows)}</span></div>
    <div class="card" style="border-top-color: #10b981;"><span class="card-label">Total Capacity</span><span class="card-val">{format_bytes(total_bytes)}</span></div>
    <div class="card" style="border-top-color: #f59e0b;"><span class="card-label">Total Objects</span><span class="card-val">{total_objects:,}</span></div>
  </div>

  <div class="table-container">
    <table id="masdrTable" class="display nowrap" style="width:100%">
      <thead>
        <tr id="headerRow">{headers}</tr>
        <tr class="filter-row"></tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <script src="https://code.jquery.com/jquery-3.7.0.js"></script>
  <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
  <script src="https://cdn.datatables.net/fixedheader/3.4.0/js/dataTables.fixedHeader.min.js"></script>

  <script>
    $(document).ready(function() {{
      const SELECT_COLS = {json.dumps(select_filter_cols)};

      // Create filter row first
      $('#masdrTable thead tr:eq(0) th').each(function (i) {{
        const title = $(this).text();
        const filterCell = $('<th></th>').appendTo('#masdrTable thead tr:eq(1)');

        if (SELECT_COLS.includes(title)) {{
          const select = $('<select><option value="">All</option></select>').appendTo(filterCell);
          select.on('change', function () {{
            const val = $.fn.dataTable.util.escapeRegex($(this).val());
            table.column(i).search(val ? '^' + val + '$' : '', true, false).draw();
          }});
        }} else {{
          $('<input type="text" placeholder="Search ' + title + '" />')
            .appendTo(filterCell)
            .on('keyup change clear', function () {{
              if (table.column(i).search() !== this.value) {{
                table.column(i).search(this.value).draw();
              }}
            }});
        }}
      }});

      var table = $('#masdrTable').DataTable({{
        orderCellsTop: true,
        fixedHeader: true,
        scrollX: true,                 // ✅ keeps header/filter aligned on wide tables
        pageLength: 50,
        dom: 'lrtip',
        drawCallback: function() {{
          const info = this.api().page.info();
          $('#statCount').text(info.recordsDisplay);
        }},
        initComplete: function () {{
          const api = this.api();

          // Populate dropdowns with unique values
          api.columns().every(function (i) {{
            const column = this;
            const title = column.header().textContent;

            if (SELECT_COLS.includes(title)) {{
              const select = $('.filter-row th:eq(' + i + ') select');
              column.data().unique().sort().each(function (d) {{
                const cleanD = (d + '').replace(/<[^>]*>?/gm, '').trim();
                if (cleanD) select.append('<option value="' + cleanD + '">' + cleanD + '</option>');
              }});
            }}
          }});

          // ✅ FIX: force columns alignment after init
          api.columns.adjust().draw(false);
        }}
      }});

      // ✅ FIX: keep alignment on resize
      $(window).on('resize', function() {{
        table.columns.adjust();
      }});
    }});
  </script>
</body>
</html>
"""

    out_base = f"masdr-oss-dashboard-({tenant})-{file_ts}"
    with open(f"{out_base}.html", "w", encoding="utf-8") as f:
        f.write(html_out)

    pd.DataFrame(data_rows).to_csv(f"{out_base}.csv", index=False)

    print(f"\n✨ SUCCESS: {out_base}.html generated with fixed/aligned header + clean focus outline.")


if __name__ == "__main__":
    main()
