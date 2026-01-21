#!/usr/bin/env python3
import json
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
import oss2

# --- 1. Credential Loader ---
def get_creds_from_json(profile_name):
    config_path = Path.home() / ".aliyun" / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at {config_path}")
    with open(config_path, 'r') as f:
        config_data = json.load(f)
    target = next((p for p in config_data.get("profiles", []) if p.get("name") == profile_name), None)
    if not target: raise ValueError(f"Profile [{profile_name}] not found.")
    return target.get("access_key_id"), target.get("access_key_secret")

# --- 2. Formatting Helpers ---
def format_bytes(b):
    try:
        b = float(b)
        if b <= 0: return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if b < 1024: return f"{b:.2f} {unit}"
            b /= 1024
        return f"{b:.2f} PB"
    except: return "0 B"

def safe_date(ts):
    if not ts: return "N/A"
    try:
        if isinstance(ts, str) and 'T' in ts: return ts.split('T')[0]
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d')
    except: return str(ts)[:10]

def _badge(val, type="acl"):
    val = str(val or "Unknown").lower()
    bg = "#64748b" 
    if type == "acl":
        bg = {"private": "#16a34a", "public-read": "#ea580c", "public-read-write": "#dc2626"}.get(val, bg)
    elif type == "redundancy":
        bg = {"zrs": "#2563eb", "lrs": "#475569"}.get(val, bg)
    return f"<span class='badge' style='background:{bg};color:white;padding:3px 8px;border-radius:4px;font-weight:bold;font-size:10px;'>{val.upper()}</span>"

# --- 3. Main Logic ---
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="me-central-1")
    args = parser.parse_args()

    tenant = args.profile
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    file_ts = datetime.now().strftime('%Y%m%d-%H%M')
    
    try:
        ak, sk = get_creds_from_json(tenant)
        auth = oss2.Auth(ak, sk)
        service = oss2.Service(auth, f'https://oss-{args.region}.aliyuncs.com')
        buckets = service.list_buckets().buckets
    except Exception as e:
        print(f"❌ Error: {e}"); return

    data_rows = []
    total_bytes, total_objects = 0, 0
    
    print(f"🚀 Generating Masdr OSS Dashboard for {tenant}...")

    for b in buckets:
        try:
            b_client = oss2.Bucket(auth, b.extranet_endpoint, b.name)
            info = b_client.get_bucket_info()
            stat = b_client.get_bucket_stat()
            
            try: versioning = b_client.get_bucket_versioning().status or "Off"
            except: versioning = "Off"
            try: accel = "Enabled" if b_client.get_bucket_transfer_acceleration().enabled else "Disabled"
            except: accel = "Disabled"
            
            total_bytes += stat.storage_size_in_bytes
            total_objects += stat.object_count
            
            data_rows.append({
                "Bucket Name": b.name,
                "Region": b.location.replace("oss-", ""),
                "Storage Class": getattr(info, 'storage_class', 'Standard'),
                "Capacity": format_bytes(stat.storage_size_in_bytes),
                "Objects": stat.object_count,
                "ACL": getattr(info.acl, 'grant', 'private'),
                "Redundancy": getattr(info, 'data_redundancy_type', 'LRS'),
                "Versioning": versioning,
                "Acceleration": accel,
                "Created At": safe_date(getattr(info, 'creation_date', None))
            })
            print(f"✅ Loaded: {b.name}")
        except: continue

    if not data_rows: return

    df = pd.DataFrame(data_rows)
    headers = "".join([f"<th>{col}</th>" for col in df.columns])
    
    rows_html = ""
    for _, r in df.iterrows():
        rows_html += "<tr>"
        for col, val in r.items():
            disp_val = _badge(val, "acl") if col == "ACL" else (_badge(val, "redundancy") if col == "Redundancy" else val)
            rows_html += f"<td>{disp_val}</td>"
        rows_html += "</tr>"

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
            .stats-bar {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .card {{ background: white; padding: 20px; border-radius: 12px; flex: 1; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border-top: 4px solid #3b82f6; }}
            .card-label {{ font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
            .card-val {{ font-size: 24px; font-weight: 800; display: block; margin-top: 5px; color: #1e293b; }}
            .table-container {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }}
            table {{ font-size: 13px; width: 100% !important; }}
            thead th {{ background: #f8fafc; color: #475569; font-weight: 700; border-bottom: 2px solid #e2e8f0 !important; }}
            .filter-row th {{ background: #ffffff !important; padding: 10px !important; }}
            select, input {{ width: 100%; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 11px; }}
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
            <table id="masdrTable" class="display nowrap">
                <thead>
                    <tr id="headerRow">{headers}</tr>
                    <tr class="filter-row"></tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>

        <script src="https://code.jquery.com/jquery-3.7.0.js"></script>
        <script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
        
        <script>
        $(document).ready(function() {{
            // Setup - add a filter input to each column in the top filter row
            $('#masdrTable thead tr:eq(0) th').each(function (i) {{
                var title = $(this).text();
                var filterCell = $('<th></th>').appendTo('#masdrTable thead tr:eq(1)');
                
                if (['Region', 'Storage Class', 'ACL', 'Redundancy', 'Versioning', 'Acceleration'].includes(title)) {{
                    var select = $('<select><option value="">All</option></select>').appendTo(filterCell);
                    // This will be populated after table init to get unique values
                    select.on('change', function () {{
                        var val = $.fn.dataTable.util.escapeRegex($(this).val());
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
                pageLength: 50,
                dom: 'lrtip',
                drawCallback: function() {{
                    // Update the visible bucket count in the card
                    var info = this.api().page.info();
                    $('#statCount').text(info.recordsDisplay);
                }},
                initComplete: function () {{
                    var api = this.api();
                    // Populate selects with unique data
                    api.columns().every(function (i) {{
                        var column = this;
                        var title = column.header().textContent;
                        if (['Region', 'Storage Class', 'ACL', 'Redundancy', 'Versioning', 'Acceleration'].includes(title)) {{
                            var select = $('.filter-row th:eq(' + i + ') select');
                            column.data().unique().sort().each(function (d) {{
                                var cleanD = d.replace(/<[^>]*>?/gm, '');
                                if (cleanD) select.append('<option value="' + cleanD + '">' + cleanD + '</option>');
                            }});
                        }}
                    }});
                }}
            }});
        }});
        </script>
    </body>
    </html>"""

    # --- 4. Final Export ---
    out_base = f"masdr-oss-dashboard-({tenant})-{file_ts}"
    with open(f"{out_base}.html", "w", encoding="utf-8") as f: f.write(html_out)
    pd.DataFrame(data_rows).to_csv(f"{out_base}.csv", index=False)
    print(f"\n✨ SUCCESS: {out_base}.html generated with Top-Column Filters.")

if __name__ == "__main__":
    main()
