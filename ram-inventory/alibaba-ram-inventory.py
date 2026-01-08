#!/usr/bin/env python3
"""
Alibaba Cloud RAM Users Report (Profile-based) -> CSV + HTML
"""

import argparse
import csv
import time
import html as html_lib
from datetime import datetime, timezone, timedelta

# SDK Imports
from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_ims20190815.client import Client as ImsClient
from alibabacloud_ims20190815 import models as ims_models
from alibabacloud_ram20150501.client import Client as RamClient
from alibabacloud_ram20150501 import models as ram_models

# --- Config ---
IMS_ENDPOINT = "ims.aliyuncs.com"
RAM_ENDPOINT = "ram.aliyuncs.com"
SLEEP_SEC = 0.15
LOCAL_TZ = timezone(timedelta(hours=3)) # Riyadh/UTC+3
DT_FMT = "%d %b %Y %H:%M:%S"

def get_clients():
    """Initializes clients using modern credential methods to avoid DeprecationWarnings."""
    cred = CredentialClient() 
    # Use get_credential() to access underlying AK/SK/Token
    credential_model = cred.get_credential()
    
    def make_config(endpoint):
        return open_api_models.Config(
            access_key_id=credential_model.access_key_id,
            access_key_secret=credential_model.access_key_secret,
            security_token=credential_model.security_token,
            endpoint=endpoint,
        )
    return ImsClient(make_config(IMS_ENDPOINT)), RamClient(make_config(RAM_ENDPOINT))

def parse_timestamp(ts) -> str:
    if not ts: return "Never"
    s = str(ts).strip()
    try:
        if s.isdigit():
            n = int(s)
            dt = datetime.fromtimestamp(n/1000.0 if n > 10**12 else n, tz=timezone.utc)
        else:
            if s.endswith("Z"): s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).strftime(DT_FMT)
    except: return s

def build_html(profile, region, headers, rows):
    esc_headers = [html_lib.escape(h) for h in headers]
    filter_cells_list = []
    for i, h in enumerate(esc_headers):
        filter_cells_list.append(f'<th><select data-col="{i}" onchange="filterTable()"><option value="">All {h}</option></select></th>')
    filter_cells = "".join(filter_cells_list)
    
    tbody_rows = ""
    for r in rows:
        tds = "".join([f"<td>{html_lib.escape(str(c))}</td>" for c in r])
        tbody_rows += f"<tr>{tds}</tr>"

    title_text = f"Alibaba RAM Audit - (Tenant: {profile}) | {region}"

    return f"""<!doctype html><html><head><meta charset="utf-8"/><title>{title_text}</title>
<style>
    body {{ font-family: -apple-system, system-ui, sans-serif; background: #f8fafc; padding: 30px; color: #1e293b; }}
    .header {{ margin-bottom: 25px; border-left: 6px solid #2563eb; padding: 20px; background: #fff; border-radius: 0 12px 12px 0; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }}
    .card {{ background: #fff; border-radius: 12px; box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1); overflow: hidden; }}
    .table-container {{ overflow-x: auto; max-height: 750px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    thead th {{ position: sticky; top: 0; background: #f1f5f9; padding: 14px; text-align: left; z-index: 10; border-bottom: 2px solid #e2e8f0; }}
    .filter-row th {{ top: 43px; background: #fff; }}
    select {{ width: 100%; padding: 5px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 10px; }}
    td {{ padding: 12px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; }}
    tr:hover {{ background: #f0f9ff; font-weight: 500; }}
    .hidden {{ display: none !important; }}
</style></head>
<body>
    <div class="header">
        <h1 style="margin:0 0 10px 0;">{title_text}</h1>
        <p style="margin:0; color:#64748b;"><strong>Audit Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Visible: <span id="v-count">{len(rows)}</span> / {len(rows)} Users</p>
    </div>
    <div class="card"><div class="table-container"><table>
        <thead>
            <tr>{"".join([f"<th>{h}</th>" for h in esc_headers])}</tr>
            <tr class="filter-row">{filter_cells}</tr>
        </thead>
        <tbody id="tBody">{tbody_rows}</tbody>
    </table></div></div>
    <script>
        const rows = Array.from(document.getElementById('tBody').rows);
        const selects = Array.from(document.querySelectorAll('select'));
        function populateFilters() {{
            selects.forEach(sel => {{
                const colIdx = sel.dataset.col;
                const vals = new Set();
                rows.forEach(r => {{ const txt = r.cells[colIdx].textContent.trim(); if(txt) vals.add(txt); }});
                Array.from(vals).sort().forEach(v => {{
                    const opt = document.createElement('option');
                    opt.value = v; opt.textContent = v;
                    sel.appendChild(opt);
                }});
            }});
        }}
        function filterTable() {{
            let count = 0;
            rows.forEach(row => {{
                let match = true;
                selects.forEach(sel => {{
                    if(sel.value && row.cells[sel.dataset.col].textContent.trim() !== sel.value) match = false;
                }});
                row.classList.toggle('hidden', !match);
                if(match) count++;
            }});
            document.getElementById('v-count').textContent = count;
        }}
        populateFilters();
    </script>
</body></html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="default", help="Alibaba CLI profile name")
    ap.add_argument("--region", default="Global", help="Region (e.g., Riyadh)")
    args = ap.parse_args()

    ims_c, ram_c = get_clients()

    # 1. Fetch IMS Metadata
    ims_map = {}
    marker = None
    while True:
        req = ims_models.ListUsersRequest(marker=marker, max_items=100)
        resp = ims_c.list_users(req).body.to_map()
        for u in (resp.get("Users", {}).get("User", []) or []):
            ims_map[u.get("UserId")] = u
        marker = resp.get("Marker")
        if not resp.get("IsTruncated"): break

    # 2. Extract User Details
    headers = [
        "Profile", "User Name", "Display Name", "Status", "User Creation Date",
        "Permissions (Direct)", "Groups", "MFA", "AccessKey Pair Status", 
        "AccessKey Pair Generated Date", "AccessKey Pair Last Used", "Last User Login"
    ]
    data_rows = []
    marker = None
    
    while True:
        req = ram_models.ListUsersRequest(marker=marker, max_items=100)
        resp = ram_c.list_users(req).body.to_map()
        for u in (resp.get("Users", {}).get("User", []) or []):
            u_name, u_id = u.get("UserName"), u.get("UserId")
            ims_u = ims_map.get(u_id, {})
            
            # User Creation Date
            user_creation_date = parse_timestamp(u.get("CreateDate"))

            # Individual Direct Permissions
            perm_list = []
            try:
                p_resp = ram_c.list_policies_for_user(ram_models.ListPoliciesForUserRequest(user_name=u_name)).body.to_map()
                perm_list = [p.get("PolicyName") for p in (p_resp.get("Policies", {}).get("Policy", []) or [])]
            except: pass
            perms_str = ", ".join(perm_list) if perm_list else "None (Group Only)"

            # Groups
            g_resp = ram_c.list_groups_for_user(ram_models.ListGroupsForUserRequest(user_name=u_name)).body.to_map()
            groups = ", ".join([g.get("GroupName") for g in (g_resp.get("Groups", {}).get("Group", []) or [])]) or "None"
            
            # Access Key Logic
            ak_status, ak_gen_date, ak_last_used = "None", "N/A", "Never"
            if ims_u.get("UserPrincipalName"):
                upn = ims_u.get("UserPrincipalName")
                ak_resp = ims_c.list_access_keys(ims_models.ListAccessKeysRequest(user_principal_name=upn)).body.to_map()
                aks = ak_resp.get("AccessKeys", {}).get("AccessKey", [])
                if aks:
                    ak_status = "Active" if any(a.get("Status") == "Active" for a in aks) else "Inactive"
                    # AccessKey Pair Generated Date (Most recent)
                    latest_ak = sorted(aks, key=lambda x: x.get("CreateDate"), reverse=True)[0]
                    ak_gen_date = parse_timestamp(latest_ak.get("CreateDate"))
                    
                    for ak in aks:
                        try:
                            lu_resp = ims_c.get_access_key_last_used(ims_models.GetAccessKeyLastUsedRequest(
                                user_access_key_id=ak.get("AccessKeyId"), 
                                user_principal_name=upn
                            )).body.to_map()
                            lu_date = parse_timestamp(lu_resp.get("AccessKeyLastUsed", {}).get("LastUsedDate"))
                            if lu_date != "Never": ak_last_used = lu_date; break
                        except: pass

            data_rows.append([
                args.profile, u_name, u.get("DisplayName"), ims_u.get("Status", "Active"), user_creation_date,
                perms_str, groups, "Yes" if ims_u.get("LastLoginDate") else "No",
                ak_status, ak_gen_date, ak_last_used, parse_timestamp(ims_u.get("LastLoginDate"))
            ])
            time.sleep(SLEEP_SEC)

        marker = resp.get("Marker")
        if not resp.get("IsTruncated"): break

    # 3. Save Files with Timestamps
    ts_file = datetime.now().strftime("%Y%m%d_%H%M")
    safe_profile = args.profile.replace(" ", "_")
    base_file = f"alibaba-ram-audit-({safe_profile})_{ts_file}"

    with open(f"{base_file}.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([headers] + data_rows)
    with open(f"{base_file}.html", "w", encoding="utf-8") as f:
        f.write(build_html(args.profile, args.region, headers, data_rows))

    print(f"Report Generated: {base_file}.html")

if __name__ == "__main__":
    main()
