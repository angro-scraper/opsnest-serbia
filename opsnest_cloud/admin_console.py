"""Private, read-only operations console for the OpsNest product owner.

This module deliberately exposes platform metadata only. It never returns
workspace tokens, password hashes, invoices, PDFs, project data, or webhook
payloads. Customer accounting remains local-first and private.
"""

from __future__ import annotations

import hmac
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import PayPalWebhookEvent, Workspace, WorkspaceAuditEvent, WorkspaceMember
from .security import sign_admin_session, verify_admin_session
from .services import effective_license
from .time_utils import utc_now


ADMIN_COOKIE = "opsnest_admin_session"
ADMIN_SESSION_HOURS = 12


def admin_session_email(request: Request) -> str | None:
    """Return the configured operator e-mail when the signed cookie is valid."""
    if not settings.admin_enabled:
        return None
    payload = verify_admin_session(request.cookies.get(ADMIN_COOKIE, ""))
    email = str((payload or {}).get("email") or "").strip().lower()
    return email if email and hmac.compare_digest(email, settings.admin_email) else None


def require_admin(request: Request) -> str:
    email = admin_session_email(request)
    if not settings.admin_enabled:
        # Do not advertise a control surface before the private credentials exist.
        raise HTTPException(status_code=404, detail="Not found.")
    if not email:
        raise HTTPException(status_code=401, detail="Administrator sign-in is required.")
    return email


def verify_admin_credentials(email: str, password: str) -> bool:
    if not settings.admin_enabled:
        return False
    normalized_email = str(email or "").strip().lower()
    # compare_digest avoids timing leaks for both values while keeping the
    # password only in the encrypted Render environment.
    return hmac.compare_digest(normalized_email, settings.admin_email) and hmac.compare_digest(
        str(password or ""), settings.admin_password
    )


def new_admin_session(email: str) -> str:
    return sign_admin_session(email, expires_in_hours=ADMIN_SESSION_HOURS)


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _license_label(status: str) -> str:
    return {
        "active": "Active",
        "trial": "Trial",
        "verification_pending": "Awaiting e-mail verification",
        "past_due": "Past due",
        "cancelled": "Cancelled",
        "suspended": "Suspended",
        "expired": "Expired",
    }.get(status, status.replace("_", " ").title() or "Unknown")


def platform_overview(db: Session) -> dict[str, Any]:
    """Build a safe operational overview without reading customer accounting data."""
    now = utc_now()
    workspaces = db.scalars(select(Workspace).order_by(Workspace.created_at.desc())).all()
    members = db.scalars(select(WorkspaceMember)).all()
    recent_audit = db.scalars(
        select(WorkspaceAuditEvent).order_by(WorkspaceAuditEvent.created_at.desc()).limit(400)
    ).all()
    recent_webhooks = db.scalars(
        select(PayPalWebhookEvent).order_by(PayPalWebhookEvent.received_at.desc()).limit(40)
    ).all()

    members_by_workspace: dict[str, list[WorkspaceMember]] = defaultdict(list)
    for member in members:
        members_by_workspace[member.workspace_id].append(member)

    latest_audit_by_workspace: dict[str, datetime] = {}
    for event in recent_audit:
        latest_audit_by_workspace.setdefault(event.workspace_id, event.created_at)

    workspace_by_id = {workspace.id: workspace for workspace in workspaces}
    workspace_by_subscription = {
        workspace.paypal_subscription_id: workspace
        for workspace in workspaces
        if workspace.paypal_subscription_id
    }
    plan_counts = {"starter": 0, "business": 0, "pro": 0}
    status_counts: dict[str, int] = defaultdict(int)
    rows: list[dict[str, Any]] = []
    verified = trial = paid = founders = active_seats = 0

    for workspace in workspaces:
        license_data = effective_license(workspace, now=now)
        status = str(license_data["status"])
        plan_code = str(license_data["plan_code"])
        plan_counts[plan_code] = plan_counts.get(plan_code, 0) + 1
        status_counts[status] += 1
        verified += int(workspace.email_verified_at is not None)
        trial += int(status == "trial")
        founders += int(license_data["access_source"] == "founder")
        paid += int(status == "active" and license_data["access_source"] == "subscription")
        workspace_members = members_by_workspace.get(workspace.id, [])
        seats_used = sum(member.status in {"active", "invited"} for member in workspace_members)
        active_seats += seats_used
        last_activity = max(
            [value for value in [workspace.updated_at, latest_audit_by_workspace.get(workspace.id)] if value],
            default=None,
        )
        member_logins = [member.last_login_at for member in workspace_members if member.last_login_at]
        if member_logins and (not last_activity or max(member_logins) > last_activity):
            last_activity = max(member_logins)
        rows.append(
            {
                "company": workspace.company_name or "Company name pending",
                "owner_email": workspace.owner_email,
                "plan": str(license_data["plan_name"]),
                "plan_code": plan_code,
                "status": status,
                "status_label": _license_label(status),
                "access_source": str(license_data["access_source"]),
                "trial_ends_at": _iso(workspace.trial_ends_at),
                "seats_used": seats_used,
                "seats_limit": 20 if plan_code == "pro" else 5 if plan_code == "business" else 1,
                "members_active": sum(member.status == "active" for member in workspace_members),
                "members_invited": sum(member.status == "invited" for member in workspace_members),
                "registered_at": _iso(workspace.created_at),
                "last_activity_at": _iso(last_activity),
            }
        )

    recent_registrations = [
        {
            "company": row["company"],
            "owner_email": row["owner_email"],
            "status": row["status_label"],
            "registered_at": row["registered_at"],
        }
        for row in rows[:12]
    ]
    activity = [
        {
            "at": _iso(event.created_at),
            "company": (workspace_by_id.get(event.workspace_id).company_name if workspace_by_id.get(event.workspace_id) else "Removed workspace"),
            "action": event.action,
            "entity": event.entity_type,
        }
        for event in recent_audit[:16]
    ]
    webhook_rows = [
        {
            "at": _iso(event.received_at),
            "event_type": event.event_type,
            "company": (workspace_by_subscription.get(event.subscription_id).company_name if workspace_by_subscription.get(event.subscription_id) else "Not linked yet"),
        }
        for event in recent_webhooks[:12]
    ]
    webhook_last_30 = sum(event.received_at >= now - timedelta(days=30) for event in recent_webhooks)
    return {
        "generated_at": _iso(now),
        "summary": {
            "companies": len(workspaces),
            "verified": verified,
            "trials": trial,
            "paid": paid,
            "founders": founders,
            "team_seats": active_seats,
            "webhooks_last_30_days": webhook_last_30,
        },
        "plans": plan_counts,
        "statuses": dict(status_counts),
        "health": {
            "environment": "Production" if settings.is_production else settings.app_env.title(),
            "database": "PostgreSQL" if settings.database_url.startswith("postgresql+") else "SQLite development database",
            "billing": "PayPal Live configured" if settings.paypal_mode == "live" and settings.paypal_client_id else "Billing needs configuration",
            "e_mail": "Resend configured" if settings.resend_api_key and settings.smtp_from_email else "E-mail delivery needs configuration",
            "desktop_release": settings.desktop_latest_version or "Not published",
        },
        "workspaces": rows,
        "recent_registrations": recent_registrations,
        "activity": activity,
        "webhooks": webhook_rows,
    }


def admin_login_html() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OpsNest Control</title><style>
    :root{--ink:#112f36;--muted:#5e7478;--teal:#07877b;--teal-dark:#056b61;--mint:#e9f6f2;--line:#d4e7e1;--paper:#fff}*{box-sizing:border-box}body{min-width:320px;margin:0;background:radial-gradient(circle at 12% 9%,#fff2cf 0,transparent 26%),radial-gradient(circle at 85% 88%,#d7f2e9 0,transparent 33%),#f5fbf9;color:var(--ink);font-family:Segoe UI,Arial,sans-serif}.page{min-height:100vh;display:grid;place-items:center;padding:28px}.card{width:min(100%,520px);padding:42px;border:1px solid var(--line);border-radius:28px;background:rgba(255,255,255,.96);box-shadow:0 25px 60px #174b4120}.brand{display:flex;align-items:center;gap:13px;color:var(--ink);text-decoration:none}.brand img{width:50px;height:50px}.brand b{display:block;font-size:1.5rem;letter-spacing:-.04em}.brand span{display:block;margin-top:3px;color:var(--muted);font-size:.84rem}.eyebrow{margin:37px 0 8px;color:var(--teal-dark);font-size:.75rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase}h1{margin:0;font-size:clamp(2rem,5vw,2.9rem);letter-spacing:-.06em;line-height:1}.lead{margin:15px 0 25px;color:var(--muted);line-height:1.55}label{display:block;margin-top:15px;color:#32545a;font-size:.88rem;font-weight:700}input{width:100%;margin-top:7px;padding:13px;border:1px solid #b7cec7;border-radius:10px;background:#fff;color:var(--ink);font:inherit}input:focus{outline:3px solid #bdece0;border-color:var(--teal)}button{width:100%;margin-top:23px;padding:14px;border:0;border-radius:10px;background:var(--teal);color:#fff;font:700 1rem inherit;cursor:pointer;box-shadow:0 10px 20px #07877b2d}button:hover{background:var(--teal-dark)}button:disabled{opacity:.6;cursor:wait}.message{min-height:22px;margin:14px 0 0;color:var(--muted);font-size:.9rem}.message.error{color:#b52f38}.note{margin:25px 0 0;padding-top:19px;border-top:1px solid var(--line);color:var(--muted);font-size:.82rem;line-height:1.5}.note b{color:var(--ink)}</style></head><body><main class="page"><section class="card"><a class="brand" href="https://opsnestone.com/"><img src="https://opsnestone.com/assets/opsnest-mark.png" alt="OpsNest"><span><b>OpsNest Control</b><span>Private platform administration</span></span></a><div class="eyebrow">Owner access only</div><h1>Platform overview, without customer accounting data.</h1><p class="lead">Sign in to review registrations, companies, package status, team seats, delivery readiness and recent operational events.</p><form id="login"><label>Administrator e-mail<input id="email" type="email" autocomplete="username" required></label><label>Administrator password<input id="password" type="password" autocomplete="current-password" required></label><button id="submit" type="submit">Open OpsNest Control</button><p id="message" class="message" role="status"></p></form><p class="note"><b>Privacy boundary:</b> this console never exposes customer invoices, PDFs, passwords, workspace tokens, payment credentials or webhook payloads.</p></section></main><script>const form=document.getElementById('login'),button=document.getElementById('submit'),message=document.getElementById('message');form.addEventListener('submit',async event=>{event.preventDefault();button.disabled=true;message.className='message';message.textContent='Checking secure administrator access...';try{const response=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({email:document.getElementById('email').value,password:document.getElementById('password').value})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Sign-in was not accepted.');window.location.assign('/admin');}catch(error){message.textContent=error.message||'Sign-in was not accepted.';message.className='message error';button.disabled=false;}});</script></body></html>"""


def admin_dashboard_html(admin_email: str) -> str:
    safe_email = escape(admin_email)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OpsNest Control</title><style>
    :root{{--ink:#112f36;--muted:#5d7479;--teal:#07877b;--teal-dark:#056b61;--mint:#e8f6f1;--line:#d5e6e1;--paper:#fff;--danger:#b8424c;--amber:#a56a10}}*{{box-sizing:border-box}}body{{min-width:320px;margin:0;color:var(--ink);background:#f6fbfa;font-family:Segoe UI,Arial,sans-serif}}.shell{{width:min(1440px,calc(100% - 40px));margin:auto;padding:28px 0 50px}}.top{{display:flex;justify-content:space-between;gap:24px;align-items:center;padding:20px 24px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(115deg,#e9f7f2,#fff9ed);box-shadow:0 13px 35px #15504812}}.brand{{display:flex;align-items:center;gap:13px;color:inherit;text-decoration:none}}.brand img{{width:47px;height:47px}}.brand b{{display:block;font-size:1.36rem;letter-spacing:-.04em}}.brand span{{display:block;margin-top:3px;color:var(--muted);font-size:.84rem}}.actions{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.operator{{color:var(--muted);font-size:.84rem}}button{{padding:10px 13px;border:1px solid #a6c9c0;border-radius:9px;background:#fff;color:var(--ink);font:700 .84rem inherit;cursor:pointer}}button.primary{{border-color:var(--teal);background:var(--teal);color:#fff}}button:hover{{border-color:var(--teal);color:var(--teal-dark)}}button.primary:hover{{background:var(--teal-dark);color:#fff}}.eyebrow{{margin:34px 0 7px;color:var(--teal-dark);font-size:.76rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}h1{{margin:0;font-size:clamp(2rem,4vw,3.2rem);letter-spacing:-.065em;line-height:1}}.lede{{max-width:760px;margin:14px 0 0;color:var(--muted);line-height:1.55}}.statusbar{{display:flex;justify-content:space-between;gap:14px;margin:27px 0 17px;padding:13px 16px;border:1px solid var(--line);border-radius:11px;background:#fff;color:var(--muted);font-size:.86rem}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}}.card{{min-height:126px;padding:18px;border:1px solid var(--line);border-radius:16px;background:var(--paper)}}.card.accent{{background:linear-gradient(135deg,#ecf8f4,#fff)}}.label{{color:var(--muted);font-size:.82rem}}.metric{{margin-top:11px;font-size:2.1rem;font-weight:800;letter-spacing:-.055em}}.sub{{margin-top:5px;color:#587176;font-size:.83rem}}.section{{margin-top:28px}}.section-title{{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:11px}}h2{{margin:0;font-size:1.28rem;letter-spacing:-.035em}}.section-title p{{margin:0;color:var(--muted);font-size:.85rem}}.health{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}}.health div{{padding:14px;border:1px solid var(--line);border-radius:12px;background:#fff}}.health span{{display:block;color:var(--muted);font-size:.76rem}}.health b{{display:block;margin-top:6px;font-size:.91rem}}.panels{{display:grid;grid-template-columns:1.35fr .85fr;gap:18px}}.panel{{overflow:hidden;border:1px solid var(--line);border-radius:16px;background:#fff}}.panel-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:17px 18px;border-bottom:1px solid var(--line)}}.panel-head input{{width:min(320px,48vw);padding:9px 11px;border:1px solid #b7cec7;border-radius:8px;font:inherit}}.table-wrap{{overflow:auto;max-height:500px}}table{{width:100%;border-collapse:collapse;font-size:.84rem}}th,td{{padding:12px 14px;border-bottom:1px solid #edf3f1;text-align:left;vertical-align:top;white-space:nowrap}}th{{position:sticky;top:0;background:#f5faf8;color:#456166;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase}}td.company{{max-width:220px;overflow:hidden;text-overflow:ellipsis;font-weight:700}}td.email{{max-width:225px;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}}.pill{{display:inline-flex;padding:4px 8px;border-radius:999px;background:#eef6f3;color:#246a5d;font-size:.74rem;font-weight:700}}.pill.warning{{background:#fff3d9;color:#8a5b0f}}.pill.off{{background:#f8e9ea;color:#9d3540}}.plan-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:16px}}.plan{{padding:15px;border:1px solid var(--line);border-radius:12px}}.plan span{{color:var(--muted);font-size:.78rem}}.plan b{{display:block;margin-top:5px;font-size:1.6rem}}.feed{{padding:0 18px 10px;list-style:none}}.feed li{{padding:12px 0;border-bottom:1px solid #edf3f1}}.feed li:last-child{{border:0}}.feed b{{display:block;font-size:.86rem}}.feed span{{display:block;margin-top:4px;color:var(--muted);font-size:.77rem;line-height:1.4}}.empty{{padding:22px 18px;color:var(--muted);font-size:.88rem}}@media(max-width:1050px){{.grid{{grid-template-columns:repeat(2,1fr)}}.health{{grid-template-columns:repeat(3,1fr)}}.panels{{grid-template-columns:1fr}}}}@media(max-width:640px){{.shell{{width:min(100% - 24px,640px);padding-top:12px}}.top{{align-items:flex-start;flex-direction:column}}.actions{{width:100%}}.operator{{flex-basis:100%}}.grid,.health{{grid-template-columns:1fr}}.statusbar,.section-title{{align-items:flex-start;flex-direction:column}}.panel-head{{align-items:flex-start;flex-direction:column}}.panel-head input{{width:100%}}}}
    </style></head><body><main class="shell"><header class="top"><a class="brand" href="https://opsnestone.com/"><img src="https://opsnestone.com/assets/opsnest-mark.png" alt="OpsNest"><span><b>OpsNest Control</b><span>Private platform administration</span></span></a><div class="actions"><span class="operator">Signed in as {safe_email}</span><button class="primary" id="refresh">Refresh data</button><button id="logout">Sign out</button></div></header><div class="eyebrow">Platform operations</div><h1>Clear status across every OpsNest workspace.</h1><p class="lede">Read-only oversight for registrations, packages, team capacity, service readiness and recent platform events. Customer accounting information remains private and local to each company.</p><div class="statusbar"><span id="loaded">Loading current platform data...</span><span>Data is never cached in this browser.</span></div><section class="grid" id="metrics"></section><section class="section"><div class="section-title"><div><h2>Service readiness</h2><p>Configuration indicators only. No credentials are displayed.</p></div></div><div class="health" id="health"></div></section><section class="section"><div class="section-title"><div><h2>Package distribution</h2><p>Active, trial and pending workspaces are included.</p></div></div><div class="panel"><div class="plan-grid" id="plans"></div></div></section><section class="section"><div class="section-title"><div><h2>Companies and registrations</h2><p>Workspace metadata only. Search by company, owner e-mail, plan or status.</p></div></div><div class="panel"><div class="panel-head"><b>All workspaces</b><input id="workspace-search" placeholder="Search companies, owner e-mails or status"></div><div class="table-wrap"><table><thead><tr><th>Company</th><th>Owner e-mail</th><th>Package</th><th>Access</th><th>Team seats</th><th>Registered</th><th>Last activity</th></tr></thead><tbody id="workspaces"></tbody></table></div></div></section><section class="section panels"><div class="panel"><div class="panel-head"><b>Recent registrations</b><span class="label">Latest twelve companies</span></div><ul class="feed" id="registrations"></ul></div><div class="panel"><div class="panel-head"><b>Recent platform activity</b><span class="label">No accounting payload</span></div><ul class="feed" id="activity"></ul></div></section><section class="section panels"><div class="panel"><div class="panel-head"><b>PayPal webhook events</b><span class="label">Event metadata only</span></div><ul class="feed" id="webhooks"></ul></div><div class="panel"><div class="panel-head"><b>Admin boundary</b></div><div class="empty">This console intentionally cannot open customer invoices, documents, PDFs, bookkeeping databases, payment credentials, browser tokens or password hashes. It is a product operations panel, not a customer data browser.</div></div></section></main><script>
    const text=value=>document.createTextNode(value||'—');const date=value=>value?new Intl.DateTimeFormat(undefined,{{dateStyle:'medium',timeStyle:'short'}}).format(new Date(value)): '—';const escapeHtml=value=>String(value||'').replace(/[&<>'"]/g,char=>({{'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}}[char]));
    let workspaceRows=[];const pill=status=>{{const normalized=String(status||'').toLowerCase();const cls=['past due','cancelled','suspended','expired','awaiting e-mail verification'].includes(normalized)?'off':normalized==='trial'?'warning':'';return '<span class="pill '+cls+'">'+escapeHtml(status)+'</span>';}};
    const renderWorkspaces=()=>{{const query=document.getElementById('workspace-search').value.toLowerCase();const rows=workspaceRows.filter(row=>Object.values(row).join(' ').toLowerCase().includes(query));document.getElementById('workspaces').innerHTML=rows.length?rows.map(row=>'<tr><td class="company">'+escapeHtml(row.company)+'</td><td class="email">'+escapeHtml(row.owner_email)+'</td><td>'+escapeHtml(row.plan)+'</td><td>'+pill(row.status_label)+(row.access_source==='founder'?' <span class="label">Founder</span>':'')+'</td><td>'+row.seats_used+' / '+row.seats_limit+'<br><span class="label">'+row.members_active+' active, '+row.members_invited+' invited</span></td><td>'+date(row.registered_at)+'</td><td>'+date(row.last_activity_at)+'</td></tr>').join(''):'<tr><td colspan="7" class="empty">No workspace matches this search.</td></tr>';}};
    const feed=(id,items,renderer,empty)=>document.getElementById(id).innerHTML=items.length?items.map(renderer).join(''):'<li class="empty">'+empty+'</li>';
    async function load(){{const response=await fetch('/admin/api/overview',{{credentials:'same-origin',cache:'no-store'}});if(response.status===401){{window.location.assign('/admin');return;}}const data=await response.json();if(!response.ok)throw Error(data.detail||'The platform overview could not be loaded.');document.getElementById('loaded').textContent='Updated '+date(data.generated_at);const summary=data.summary;const metrics=[['Registered companies',summary.companies,'All workspaces'],['Verified companies',summary.verified,'Confirmed business e-mails'],['Active trials',summary.trials,'Seven-day trial access'],['Paid subscriptions',summary.paid,'Active customer subscriptions'],['Founder access',summary.founders,'Internal full-access workspaces'],['Team seats in use',summary.team_seats,'Active and invited members'],['Webhook events',summary.webhooks_last_30_days,'Received in the last 30 days']];document.getElementById('metrics').innerHTML=metrics.map((item,index)=>'<article class="card '+(index===0?'accent':'')+'"><div class="label">'+item[0]+'</div><div class="metric">'+item[1]+'</div><div class="sub">'+item[2]+'</div></article>').join('');document.getElementById('health').innerHTML=Object.entries(data.health).map(([key,value])=>'<div><span>'+escapeHtml(key.replaceAll('_',' '))+'</span><b>'+escapeHtml(value)+'</b></div>').join('');document.getElementById('plans').innerHTML=['starter','business','pro'].map(code=>'<article class="plan"><span>'+code.charAt(0).toUpperCase()+code.slice(1)+' workspaces</span><b>'+Number(data.plans[code]||0)+'</b></article>').join('');workspaceRows=data.workspaces;renderWorkspaces();feed('registrations',data.recent_registrations,item=>'<li><b>'+escapeHtml(item.company)+'</b><span>'+escapeHtml(item.owner_email)+' · '+escapeHtml(item.status)+'<br>'+date(item.registered_at)+'</span></li>','No registrations yet.');feed('activity',data.activity,item=>'<li><b>'+escapeHtml(item.action)+'</b><span>'+escapeHtml(item.company)+' · '+escapeHtml(item.entity||'workspace')+'<br>'+date(item.at)+'</span></li>','No operational activity yet.');feed('webhooks',data.webhooks,item=>'<li><b>'+escapeHtml(item.event_type)+'</b><span>'+escapeHtml(item.company)+'<br>'+date(item.at)+'</span></li>','No PayPal webhook event has been received yet.');}}
    document.getElementById('workspace-search').addEventListener('input',renderWorkspaces);document.getElementById('refresh').addEventListener('click',()=>load().catch(error=>alert(error.message)));document.getElementById('logout').addEventListener('click',async()=>{{await fetch('/admin/logout',{{method:'POST',credentials:'same-origin'}});window.location.assign('/admin');}});load().catch(error=>{{document.getElementById('loaded').textContent=error.message||'The platform overview could not be loaded.';}});
    </script></body></html>"""
