#!/usr/bin/env python3
import html
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd

from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_tea_openapi import models as open_api_models

# --- 1. Runtime Configuration ---
def parse_args():
    parser = argparse.ArgumentParser(description="Alibaba Cloud ECS Inventory Reporter")
    # Profile/Tenant is mandatory
    parser.add_argument("--profile", required=True, help="REQUIRED: The Alibaba Cloud profile/tenant name")
    parser.add_argument("--region", default="me-central-1", help="Region ID (default: me-central-1)")
    return parser.parse_args()

ARGS = parse_args()
TENANT_NAME = ARGS.profile  
REGION_ID = ARGS.region
REPORT_DATE = datetime.now().strftime("%Y-%m-%d")

# --- 2. Safety & UI Helpers ---
def _s(val):
    return str(val).strip() if val is not None else ""

def _safe_join(vals):
    if not vals: return ""
    return "; ".join(dict.fromkeys([str(v) for v in vals if v]))

def _badge(state):
    st = _s(state)
    colors = {"Running": "#16a34a", "Stopped": "#dc2626", "Starting": "#2563eb", "Stopping": "#ea580c"}
    c = colors.get(st, "#64748b")
    return f"<span style='background:{c};color:white;padding:2px 10px;border-radius:20px;font-size:10px;font-weight:bold'>{html.escape(st)}</span>"

def _ecs_link(iid):
    sid = html.escape(_s(iid))
    url = f"https://ecs.console.aliyun.com/#/server/{REGION_ID}/{sid}/detail"
    return f"<a href='{url}' data-value='{sid}' target='_blank' style='color:#2563eb;text-decoration:none;font-weight:600'>{sid}</a>"

# --- 3. HTML Writer ---
def write_html_report(df, path, summary):
    cols = list(df.columns)
    rows_html = "".join([f"<tr>{''.join(f'<td>{r[c]}</td>' for c in cols)}</tr>" for _, r in df.iterrows()])
    th_html = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    filters = "".join(f"<th><select class='f' data-col='{i}'><option value='ALL'>All</option></select></th>" for i in range(len(cols)))

    html_content = f"""<!DOCTYPE html>
    <html lang="en"><head><meta charset="utf-8">
    <title>Alibaba ECS Inventory (Tenant: {TENANT_NAME}) - {REPORT_DATE}</title>
    <style>
        body {{ font-family: 'Inter', system-ui, sans-serif; background: #f8fafc; margin: 0; padding: 25px; color: #1e293b; font-size: 11px; }}
        .card {{ background: white; padding: 24px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); border: 1px solid #e2e8f0; }}
        h2 {{ margin: 0 0 8px 0; color: #0f172a; font-size: 18px; border-bottom: 2px solid #f1f5f9; padding-bottom: 12px; }}
        .meta-bar {{ color: #64748b; margin-bottom: 20px; font-size: 13px; }}
        .meta-bar b {{ color: #1e3a8a; }}
        .stats {{ display: flex; gap: 15px; margin-bottom: 20px; }}
        .stat-box {{ background: #f8fafc; padding: 12px; border-radius: 8px; flex: 1; border: 1px solid #e2e8f0; }}
        .stat-label {{ font-size: 9px; text-transform: uppercase; color: #64748b; font-weight: 700; margin-bottom: 4px; }}
        .stat-val {{ font-size: 16px; font-weight: 800; color: #1e3a8a; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
        th, td {{ border: 1px solid #e2e8f0; padding: 8px 10px; text-align: left; white-space: nowrap; }}
        th {{ background: #f8fafc; position: sticky; top: 0; z-index: 10; font-weight: 600; color: #475569; }}
        #q {{ width: 300px; padding: 8px; margin-bottom: 15px; border: 1px solid #cbd5e1; border-radius: 6px; outline: none; }}
        .f {{ width: 100%; font-size: 10px; border: 1px solid #e2e8f0; cursor: pointer; }}
    </style></head>
    <body><div class="card">
        <h2>Alibaba ECS Inventory (Tenant: {TENANT_NAME}) - {REPORT_DATE}</h2>
        <div class="meta-bar">Tenant: <b>{TENANT_NAME}</b> &nbsp;|&nbsp; Region: <b>{REGION_ID}</b></div>
        <div class="stats">
            <div class="stat-box"><div class="stat-label">Total Instances</div><div class="stat-val">{summary['count']}</div></div>
            <div class="stat-box"><div class="stat-label">Total vCPU</div><div class="stat-val">{summary['vcpu']}</div></div>
            <div class="stat-box"><div class="stat-label">Total RAM</div><div class="stat-val">{summary['mem']} GB</div></div>
            <div class="stat-box"><div class="stat-label">Total Storage</div><div class="stat-val">{summary['disk']} GB</div></div>
        </div>
        <input id="q" placeholder="🔍 Search Instance Name, IP, etc..." />
        <div style="overflow-x: auto;">
            <table id="t"><thead><tr>{th_html}</tr><tr class="filters">{filters}</tr></thead>
            <tbody>{rows_html}</tbody></table>
        </div>
    </div>
    <script>
        const t=document.getElementById("t"), qs=document.getElementById("q"), sels=document.querySelectorAll(".f");
        function getV(c){{ const a=c.querySelector("a"); return (a?a.dataset.value:c.textContent).trim(); }}
        function update(){{
            const q=qs.value.toLowerCase(), rows=Array.from(t.tBodies[0].rows);
            rows.forEach(r=>{{
                let s=(q==="" || r.textContent.toLowerCase().includes(q));
                sels.forEach(sel=>{{
                    if(!s || sel.value==="ALL") return;
                    if(getV(r.cells[sel.dataset.col])!==sel.value) s=false;
                }});
                r.style.display=s?"":"none";
            }});
        }}
        sels.forEach(s=>{{
            const col=s.dataset.col, vals=new Set();
            Array.from(t.tBodies[0].rows).forEach(r=>vals.add(getV(r.cells[col])));
            Array.from(vals).sort().forEach(v=>{{ if(v!=="") {{ const o=document.createElement("option"); o.value=o.textContent=v; s.appendChild(o); }} }});
            s.onchange=update;
        }});
        qs.oninput=update;
    </script></body></html>"""
    with open(path, "w", encoding="utf-8") as f: f.write(html_content)

# --- 4. Main Process ---
def main():
    print(f"🚀 Running Inventory for Tenant: {TENANT_NAME}")

    try:
        os.environ["ALIBABA_CLOUD_PROFILE"] = TENANT_NAME
        client = EcsClient(open_api_models.Config(region_id=REGION_ID, credential=CredentialClient()))
    except Exception as e:
        print(f"❌ Connection Failed for profile '{TENANT_NAME}': {e}"); return

    data_csv, data_html, page = [], [], 1
    sum_vcpu = sum_mem = sum_disk = 0

    while True:
        req = ecs_models.DescribeInstancesRequest(region_id=REGION_ID, page_number=page, page_size=50)
        resp = client.describe_instances(req).body
        insts = resp.instances.instance if resp and resp.instances else []
        if not insts: break

        for ins in insts:
            iid = _s(ins.instance_id)
            ins_name = _s(ins.instance_name) or "Unnamed"
            vcpu = int(ins.cpu or 0)
            mem = round(float(ins.memory or 0)/1024, 2)
            
            d_tot = d_sys = d_dat = 0
            d_cats, d_ids = [], []
            snap_count = 0; latest_snap_time = ""
            
            try:
                # Disk Data
                d_resp = client.describe_disks(ecs_models.DescribeDisksRequest(region_id=REGION_ID, instance_id=iid)).body
                for d in d_resp.disks.disk:
                    sz = int(d.size or 0)
                    d_tot += sz
                    d_ids.append(d.disk_id)
                    d_cats.append(d.category)
                    if _s(d.type).lower() == "system": d_sys += sz
                    else: d_dat += sz
                
                # Snapshot Data
                s_resp = client.describe_snapshots(ecs_models.DescribeSnapshotsRequest(region_id=REGION_ID, instance_id=iid)).body
                if s_resp and s_resp.snapshots:
                    snaps = s_resp.snapshots.snapshot
                    snap_count = len(snaps)
                    times = [s.creation_time for s in snaps if s.creation_time]
                    if times: latest_snap_time = max(times)
            except: pass
            
            sum_vcpu += vcpu; sum_mem += mem; sum_disk += d_tot

            # Note renamed first column: Instance Name
            row = [ins_name, iid, _s(ins.instance_type), vcpu, mem, _s(getattr(ins, 'osname_en', "")), d_tot, d_sys, d_dat, _safe_join(d_cats), snap_count, latest_snap_time, _s(ins.status), _s(ins.zone_id), _s(ins.vpc_attributes.vpc_id if ins.vpc_attributes else ""), _s(ins.creation_time)]
            data_csv.append(row)

            data_html.append([
                html.escape(ins_name), _ecs_link(iid), html.escape(_s(ins.instance_type)), vcpu, mem, html.escape(_s(getattr(ins, 'osname_en', ""))),
                d_tot, d_sys, d_dat, html.escape(_safe_join(d_cats)), snap_count, latest_snap_time, 
                _badge(ins.status), html.escape(_s(ins.zone_id)), html.escape(_s(ins.vpc_attributes.vpc_id if ins.vpc_attributes else "")), html.escape(_s(ins.creation_time))
            ])

        if page * 50 >= (resp.total_count or 0): break
        page += 1

    summary = {"count": len(data_csv), "vcpu": sum_vcpu, "mem": round(sum_mem, 2), "disk": sum_disk}
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    base_name = f"ecs-inventory-({TENANT_NAME})-{ts}"
    
    # Updated Headers
    heads = ["Instance Name", "InstanceId", "Type", "vCPU", "RAM(GB)", "OS", "DiskTotal", "SystemDisk", "DataDisk", "Categories", "Snapshots", "LatestSnap", "State", "Zone", "VPC", "Created"]
    pd.DataFrame(data_csv, columns=heads).to_csv(f"{base_name}.csv", index=False)
    write_html_report(pd.DataFrame(data_html, columns=heads), f"{base_name}.html", summary)
    
    print(f"✅ Success! Report for {TENANT_NAME} generated: {base_name}.html")

if __name__ == "__main__":
    main()
