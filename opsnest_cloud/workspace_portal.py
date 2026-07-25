"""Minimal owner and accountant workspace portal.

The portal is intentionally a collaboration surface, not a second accounting
database. It exposes only authenticated workspace metadata and keeps invoices,
attachments and financial snapshots out of browser HTML.
"""

from __future__ import annotations


def workspace_portal_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpsNest Workspace</title>
<style>
:root{--ink:#132238;--muted:#63738a;--line:#dce5ee;--mint:#087e72;--mint-soft:#e8f7f3;--navy:#102d47;--bg:#f5f8fb;--card:#fff;--warn:#9b6510}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:15px/1.5 Inter,Segoe UI,Arial,sans-serif;color:var(--ink)}
.shell{max-width:1180px;margin:auto;padding:30px 22px 70px}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px}.brand{display:flex;gap:11px;align-items:center;font-weight:800;font-size:20px}.mark{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,#08a898,#075a78);position:relative}.mark:after{content:'N';color:white;position:absolute;left:10px;top:5px;font-weight:900;font-size:20px}.tag{color:var(--mint);font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 26px #102d470c}.login{max-width:510px;margin:8vh auto;padding:30px}.login h1{margin:6px 0;font-size:29px}.muted{color:var(--muted)}label{display:block;font-size:13px;font-weight:700;margin-top:14px}input,select{width:100%;border:1px solid #bdcad7;border-radius:9px;padding:11px 12px;margin-top:5px;font:inherit;background:white;color:var(--ink)}button{border:0;border-radius:9px;padding:11px 15px;font:inherit;font-weight:750;cursor:pointer}.primary{background:var(--mint);color:#fff}.quiet{background:#eef3f7;color:var(--navy)}.link{background:transparent;color:var(--mint);padding:10px 0}.actions{display:flex;gap:8px;align-items:center;margin-top:20px}.error{color:#b42318;margin-top:12px;min-height:22px}.hero{padding:28px;background:linear-gradient(120deg,#10304b,#087e72);color:white;border-radius:18px}.hero h1{font-size:31px;margin:4px 0}.hero p{margin:0;color:#d6f5ee}.workspace-nav{display:flex;gap:8px;overflow:auto;margin:15px 0}.workspace-nav button{white-space:nowrap;padding:9px 12px;background:#fff;border:1px solid var(--line);color:var(--navy)}.workspace-nav button:hover{border-color:var(--mint);color:var(--mint)}.command-grid{display:grid;grid-template-columns:1.25fr repeat(3,1fr);gap:12px;margin:16px 0}.command-card{border:1px solid var(--line);border-radius:14px;padding:17px;background:#fff}.command-card b{display:block;font-size:15px}.command-card p{margin:5px 0 0;color:var(--muted);font-size:13px}.command-card.accent{background:linear-gradient(135deg,#e8f7f3,#fff);border-color:#b8e4dc}.command-card button{margin-top:12px;padding:8px 10px;font-size:12px}.process{margin:16px 0;padding:20px}.process h2{margin:0 0 4px;font-size:18px}.process p{margin:0;color:var(--muted)}.process-steps{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:16px}.process-step{padding:13px;border-radius:12px;background:#f6fafb;border:1px solid var(--line);font-size:13px}.process-step b{display:block;color:var(--mint);font-size:12px;margin-bottom:4px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:16px 0}.metric{padding:18px}.metric b{font-size:22px;display:block;margin-top:8px}.metric span{font-size:13px;color:var(--muted)}.two{display:grid;grid-template-columns:1.5fr 1fr;gap:16px}.section{padding:22px}.section h2{font-size:18px;margin:0 0 14px}.module{display:flex;justify-content:space-between;gap:18px;padding:15px 0;border-top:1px solid var(--line)}.module:first-of-type{border-top:0}.module b{display:block}.module p{margin:3px 0 0;color:var(--muted);font-size:13px}.status{height:max-content;border-radius:99px;padding:4px 9px;background:var(--mint-soft);color:#06665d;font-size:12px;font-weight:800;white-space:nowrap}.status.foundation{background:#fff6df;color:var(--warn)}.profile{display:grid;grid-template-columns:1fr 1fr;gap:10px}.profile .wide{grid-column:1/-1}.queue{margin:16px 0}.queue-list{display:grid;gap:10px;margin-top:14px}.queue-item{border:1px solid var(--line);border-radius:11px;padding:14px;display:flex;gap:14px;justify-content:space-between;align-items:flex-start}.queue-item p{margin:4px 0 0;color:var(--muted);font-size:13px}.queue-item select{width:auto;min-width:132px;padding:7px;margin:0}.queue-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.small{padding:7px 10px;font-size:12px}.footer{color:var(--muted);font-size:12px;margin-top:20px}.language-picker{display:flex;gap:8px;align-items:center}.language-picker select{width:auto;margin:0;padding:8px 9px;font-size:13px}.hidden{display:none!important}@media(max-width:780px){.command-grid{grid-template-columns:repeat(2,1fr)}.process-steps{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}.hero h1{font-size:25px}.queue-item{display:block}.queue-actions{margin-top:10px}}@media(max-width:460px){.command-grid,.grid{grid-template-columns:1fr}.profile{grid-template-columns:1fr}.top{align-items:flex-start;gap:10px}.shell{padding:18px 14px}}
</style>
</head>
<body>
<main class="shell">
<header class="top">
<div class="brand">
<i class="mark">
</i>OpsNest <span class="tag">Workspace</span>
</div>
<div class="language-picker">
<label for="language" class="muted" style="margin:0">Language</label>
<select id="language" aria-label="Language">
<option value="sr">Srpski</option>
<option value="en">English</option>
</select>
<button id="logout" class="quiet hidden">Sign out</button>
</div>
</header>
<section id="loginView" class="card login">
<div class="tag">Secure company access</div>
<h1>One place for the owner, accountant and team.</h1>
<p class="muted">Use the central team account created in OpsNest Desktop. Financial documents remain protected in the company workspace.</p>
<form id="loginForm">
<label>Workspace ID<input id="workspaceId" autocomplete="organization" required placeholder="UUID from OpsNest Desktop">
</label>
<label>Business e-mail<input id="email" type="email" autocomplete="email" required>
</label>
<label>Password<input id="password" type="password" autocomplete="current-password" required>
</label>
<div class="actions">
<button class="primary" type="submit">Open workspace</button>
</div>
<button id="showReset" class="link" type="button">Forgot password?</button>
<p id="loginError" class="error">
</p>
</form>
<form id="resetForm" class="hidden">
<p class="muted">We will send a six-digit, one-time code to the business e-mail. Existing sessions will be signed out after the password changes.</p>
<label>Workspace ID<input id="resetWorkspaceId" required>
</label>
<label>Business e-mail<input id="resetEmail" type="email" required>
</label>
<label>Recovery code<input id="resetCode" inputmode="numeric" maxlength="6">
</label>
<label>New password<input id="resetPassword" type="password" minlength="10">
</label>
<div class="actions">
<button id="sendReset" class="quiet" type="button">Send recovery code</button>
<button class="primary" type="submit">Set new password</button>
</div>
<button id="backToLogin" class="link" type="button">Back to sign in</button>
<p id="resetStatus" class="error">
</p>
</form>
</section>
<section id="appView" class="hidden">
<div class="hero">
<div class="tag" style="color:#a9f5e8">Connected workspace</div>
<h1 id="companyName">Your OpsNest workspace</h1>
<p id="heroCopy">Loading collaboration controls…</p>
</div>
<nav class="workspace-nav" aria-label="Workspace navigation">
<button type="button" data-scroll="commandCenter">Overview</button>
<button type="button" data-scroll="workflowList">Work and approvals</button>
<button type="button" data-scroll="documentList">Documents</button>
<button type="button" data-scroll="teamSection">Team</button>
<button type="button" data-scroll="auditSection">Controls</button>
<button type="button" data-scroll="modules">Platform roadmap</button>
</nav>
<section id="commandCenter" class="command-grid">
<article class="command-card accent"><b>Owner command center</b><p>One clear place for what needs attention, who owns it and what is ready for review.</p><button class="primary" type="button" data-scroll="workflowList">Open work queue</button></article>
<article class="command-card"><b>Finance centre</b><p>Suppliers, payables, cash, forecast, approvals and period close are managed in OpsNest Desktop today.</p><button class="quiet" type="button" data-scroll="modules">See finance readiness</button></article>
<article class="command-card"><b>Document control</b><p>Private document intake is ready to activate when your EU storage policy is chosen.</p><button class="quiet" type="button" data-scroll="documentList">Open Document Inbox</button></article>
<article class="command-card"><b>Team continuity</b><p>Roles, password recovery, task assignment and control trail keep work moving when one person is away.</p><button class="quiet" type="button" data-scroll="teamSection">Open team controls</button></article>
</section>
<section class="card process"><h2>One controlled business flow</h2><p>Every stage is visible to the owner and ready for the accountant. External connectors are activated only when your company enables them.</p><div class="process-steps"><div class="process-step"><b>01 · PLAN</b>Project, contract and budget</div><div class="process-step"><b>02 · DOCUMENT</b>Invoice, receipt or contract</div><div class="process-step"><b>03 · APPROVE</b>Responsible person and owner control</div><div class="process-step"><b>04 · PAY</b>Cash, bank and payable control</div><div class="process-step"><b>05 · CLOSE</b>Reports, audit and local tax module</div></div></section>
<div id="metrics" class="grid">
</div>
<section class="card section queue">
<h2>Operational work queue</h2>
<p class="muted">Assign document checks, payment preparation, VAT controls and reviews. The queue contains only operational metadata — never invoice files.</p>
<form id="queueForm" class="profile hidden">
<label class="wide">Work item<input id="queueTitle" maxlength="240" placeholder="Example: Verify supplier invoice before payment" required>
</label>
<label>Type<select id="queueType">
<option value="document">Document check</option>
<option value="payment">Payment</option>
<option value="vat">VAT control</option>
<option value="review">Review</option>
<option value="other">Other</option>
</select>
</label>
<label>Priority<select id="queuePriority">
<option value="normal">Normal</option>
<option value="low">Low</option>
<option value="high">High</option>
<option value="urgent">Urgent</option>
</select>
</label>
<label>Due date<input id="queueDue" type="date">
</label>
<label>Responsible person<select id="queueAssignee">
</select>
</label>
<button class="primary wide" type="submit">Add to work queue</button>
</form>
<p id="queueStatus" class="error">
</p>
<div id="workflowList" class="queue-list">
</div>
</section>
<section class="card section queue">
<h2>Document Inbox</h2>
<p class="muted">Private PDF, JPEG and PNG files only. The database stores metadata; the file itself stays in the private document bucket.</p>
<form id="documentForm" class="profile">
<label class="wide">File<input id="documentFile" type="file" accept="application/pdf,image/jpeg,image/png" required>
</label>
<label>Type<select id="documentType">
<option value="invoice">Invoice</option>
<option value="receipt">Receipt</option>
<option value="contract">Contract</option>
<option value="statement">Bank statement</option>
<option value="other">Other</option>
</select>
</label>
<label>Link to work item<select id="documentWorkflow">
<option value="">No work item</option>
</select>
</label>
<button class="primary wide" type="submit">Upload securely</button>
</form>
<p id="documentStatus" class="error">
</p>
<div id="documentList" class="queue-list">
</div>
</section>
<section id="teamSection" class="card section queue hidden">
<h2>Company team</h2>
<p class="muted">Invite the right role, see access status and revoke access immediately when responsibilities change.</p>
<form id="inviteForm" class="profile">
<label>Full name<input id="inviteName" maxlength="160" required>
</label>
<label>Business e-mail<input id="inviteEmail" type="email" maxlength="320" required>
</label>
<label class="wide">Role<select id="inviteRole">
<option value="accountant">Accountant</option>
<option value="administrator">Administrator</option>
<option value="project_manager">Project manager</option>
<option value="operator">Operator</option>
</select>
</label>
<button class="primary wide" type="submit">Send secure invitation</button>
</form>
<p id="teamStatus" class="error">
</p>
<div id="teamList" class="queue-list">
</div>
</section>
<section id="auditSection" class="card section queue hidden">
<h2>Control trail</h2>
<p class="muted">Recent operational events for this company. Passwords, invoices, files and payment credentials never appear here.</p>
<div id="auditList" class="queue-list">
</div>
</section>
<div class="two">
<section class="card section">
<h2>Platform modules</h2>
<div id="modules">
</div>
</section>
<section class="card section">
<h2>Company country pack</h2>
<p id="countrySummary" class="muted">
</p>
<form id="profileForm" class="profile hidden">
<label>Country code<select id="countryCode">
<option value="RS">Serbia</option>
<option value="BG">Bulgaria</option>
<option value="HR">Croatia</option>
<option value="BA">Bosnia and Herzegovina</option>
<option value="ME">Montenegro</option>
<option value="MK">North Macedonia</option>
<option value="SI">Slovenia</option>
<option value="INTL">International</option>
</select>
</label>
<label>Currency<input id="currency" maxlength="8">
</label>
<label class="wide">Business profile<select id="profile">
<option value="general">General business</option>
<option value="construction">Construction</option>
<option value="services">Services</option>
<option value="trade">Trade</option>
</select>
</label>
<button class="primary wide" type="submit">Save platform profile</button>
</form>
</section>
</div>
<p class="footer">OpsNest Workspace shows secure collaboration metadata. Invoices, attachments and accounting records are never rendered in this browser portal without a dedicated country-specific module.</p>
</section>
</main><script>
const LANGUAGE_KEY='opsnestWorkspaceLanguage';let language=localStorage.getItem(LANGUAGE_KEY)||'sr';
const SR_TRANSLATIONS=Object.freeze({
 'Language':'Jezik','Workspace':'Radni prostor','Sign out':'Odjavi se','Secure company access':'Bezbedan pristup firmi','One place for the owner, accountant and team.':'Jedno mesto za vlasnika, knjigovođu i tim.','Use the central team account created in OpsNest Desktop. Financial documents remain protected in the company workspace.':'Koristite centralni timski nalog napravljen u OpsNest Desktop aplikaciji. Finansijski dokumenti ostaju zaštićeni u radnom prostoru firme.','Workspace ID':'ID radnog prostora','UUID from OpsNest Desktop':'UUID iz OpsNest Desktop aplikacije','Business e-mail':'Poslovni e-mail','Password':'Lozinka','Open workspace':'Otvori radni prostor','Forgot password?':'Zaboravili ste lozinku?','We will send a six-digit, one-time code to the business e-mail. Existing sessions will be signed out after the password changes.':'Poslaćemo jednokratni šestocifreni kod na poslovni e-mail. Sve postojeće sesije biće odjavljene nakon promene lozinke.','Recovery code':'Kod za oporavak','New password':'Nova lozinka','Send recovery code':'Pošalji kod za oporavak','Set new password':'Postavi novu lozinku','Back to sign in':'Nazad na prijavu','Connected workspace':'Povezan radni prostor','Your OpsNest workspace':'Vaš OpsNest radni prostor','Loading collaboration controls…':'Učitavanje kontrola saradnje…','Operational work queue':'Operativna radna lista','Assign document checks, payment preparation, VAT controls and reviews. The queue contains only operational metadata — never invoice files.':'Dodelite proveru dokumenata, pripremu plaćanja, PDV kontrole i preglede. Lista sadrži samo operativne podatke — nikada fajlove faktura.','Work item':'Radni zadatak','Example: Verify supplier invoice before payment':'Primer: Proveri ulaznu fakturu pre plaćanja','Type':'Tip','Priority':'Prioritet','Due date':'Rok','Responsible person':'Odgovorna osoba','Add to work queue':'Dodaj u radnu listu','Document check':'Provera dokumenta','Payment':'Plaćanje','VAT control':'PDV kontrola','Review':'Pregled','Other':'Ostalo','Normal':'Normalan','Low':'Nizak','High':'Visok','Urgent':'Hitan','Open':'Otvoren','In progress':'U toku','Waiting':'Na čekanju','Done':'Završen','Unassigned':'Nedodeljeno','Comment':'Komentar','History':'Istorija','No work items yet. Start with the next payment, document or VAT check.':'Još nema radnih zadataka. Počnite od sledećeg plaćanja, dokumenta ili PDV kontrole.','Document Inbox':'Prijem dokumenata','Private PDF, JPEG and PNG files only. The database stores metadata; the file itself stays in the private document bucket.':'Dozvoljeni su samo privatni PDF, JPEG i PNG fajlovi. Baza čuva metapodatke, a sam fajl ostaje u privatnoj arhivi dokumenata.','File':'Fajl','Invoice':'Faktura','Receipt':'Račun','Contract':'Ugovor','Bank statement':'Izvod banke','Link to work item':'Poveži sa radnim zadatkom','No work item':'Bez radnog zadatka','Upload securely':'Bezbedno otpremi','Download':'Preuzmi','No uploaded documents.':'Nema otpremljenih dokumenata.','Company team':'Tim firme','Invite the right role, see access status and revoke access immediately when responsibilities change.':'Pozovite odgovarajuću ulogu, vidite status pristupa i odmah ukinite pristup kada se odgovornosti promene.','Full name':'Ime i prezime','Role':'Uloga','Accountant':'Knjigovođa','Administrator':'Administrator','Project manager':'Menadžer projekta','Operator':'Operater','Send secure invitation':'Pošalji bezbedan poziv','Revoke access':'Ukinite pristup','No team members yet.':'Još nema članova tima.','Control trail':'Kontrolni trag','Recent operational events for this company. Passwords, invoices, files and payment credentials never appear here.':'Najnoviji operativni događaji za ovu firmu. Lozinke, fakture, fajlovi i podaci za plaćanje se ovde nikada ne prikazuju.','No operational events recorded yet.':'Još nema zabeleženih operativnih događaja.','Platform modules':'Moduli platforme','Company country pack':'Nacionalni paket firme','Country code':'Kod države','Serbia':'Srbija','Bulgaria':'Bugarska','Croatia':'Hrvatska','Bosnia and Herzegovina':'Bosna i Hercegovina','Montenegro':'Crna Gora','North Macedonia':'Severna Makedonija','Slovenia':'Slovenija','International':'Međunarodno','Currency':'Valuta','Business profile':'Poslovni profil','General business':'Opšte poslovanje','Construction':'Građevinarstvo','Services':'Usluge','Trade':'Trgovina','Save platform profile':'Sačuvaj profil platforme','OpsNest Workspace shows secure collaboration metadata. Invoices, attachments and accounting records are never rendered in this browser portal without a dedicated country-specific module.':'OpsNest radni prostor prikazuje zaštićene podatke za saradnju. Fakture, prilozi i knjigovodstvena dokumentacija se ne prikazuju u ovom portalu bez posebnog modula za izabranu državu.','Plan':'Paket','Team seats':'Mesta u timu','Cloud sync':'Cloud sinhronizacija','Not connected yet':'Još nije povezano','Revision':'Revizija','Projects and contracts':'Projekti i ugovori','Operational project records stay available in OpsNest Desktop.':'Operativni podaci o projektima ostaju dostupni u OpsNest Desktop aplikaciji.','Assign document checks, payments, VAT controls and reviews with comments and deadlines.':'Dodelite provere dokumenata, plaćanja, PDV kontrole i preglede uz komentare i rokove.','Private PDF/image intake is ready after the EU document-storage bucket is configured.':'Privatni prijem PDF i slika je spreman nakon podešavanja EU arhive dokumenata.','Money and cash-flow':'Novac i novčani tok','Bank, cash and forecasts remain in the controlled desktop workspace.':'Banka, kasa i prognoze ostaju u kontrolisanom Desktop radnom prostoru.','Accountant collaboration':'Saradnja sa knjigovođom','Team roles, access control and audit are active.':'Uloge tima, kontrola pristupa i kontrolni trag su aktivni.','desktop':'desktop','ready':'spremno','configuration_required':'potrebno podešavanje','member':'član','VAT and fiscalization foundation':'Osnova za PDV i fiskalizaciju','Country-pack foundation':'Osnova nacionalnog paketa','E-invoice and VAT foundation':'Osnova za e-fakture i PDV','Document storage is not enabled yet. No files can be uploaded until the private bucket is configured.':'Arhiva dokumenata još nije aktivirana. Fajlovi se ne mogu otpremiti dok se ne podesi privatni prostor za skladištenje.','Uploading to private document storage…':'Otpremanje u privatnu arhivu dokumenata…','Document stored securely.':'Dokument je bezbedno sačuvan.','Invitation sent. It expires ':'Poziv je poslat. Ističe ','Add an operational comment. Do not enter passwords, invoice files or payment credentials.':'Dodajte operativni komentar. Ne unosite lozinke, fajlove faktura niti podatke za plaćanje.','No comments yet.':'Još nema komentara.','Revoke this person’s access immediately? Their active sessions will be signed out.':'Odmah ukinuti pristup ovoj osobi? Njene aktivne sesije biće odjavljene.','Sending recovery code…':'Slanje koda za oporavak…','Changing password…':'Promena lozinke…','Sign-in failed.':'Prijava nije uspela.','The workspace request could not be completed.':'Zahtev radnog prostora nije mogao da se izvrši.','Recovery request could not be completed.':'Zahtev za oporavak nije mogao da se izvrši.','Password could not be changed.':'Lozinka nije mogla da se promeni.','Upload failed.':'Otpremanje nije uspelo.','E-mail or password is not correct.':'E-mail ili lozinka nisu ispravni.','Invalid or expired team session.':'Nevažeća ili istekla sesija tima.','Wait one minute before requesting another recovery code.':'Sačekajte jedan minut pre nego što zatražite novi kod za oporavak.','Recovery code expired. Request a new code.':'Kod za oporavak je istekao. Zatražite novi kod.','Too many attempts. Request a new recovery code.':'Previše pokušaja. Zatražite novi kod za oporavak.','Recovery code is not correct.':'Kod za oporavak nije ispravan.','Password reset e-mail is not configured yet.':'E-mail za obnovu lozinke još nije podešen.','Owner / Administrator':'Vlasnik / administrator','due':'rok'
});
const SR_DASHBOARD_TRANSLATIONS=Object.freeze({'Overview':'Pregled','Work and approvals':'Rad i odobravanja','Documents':'Dokumenti','Team':'Tim','Controls':'Kontrole','Platform roadmap':'Plan razvoja platforme','Owner command center':'Komandni centar vlasnika','One clear place for what needs attention, who owns it and what is ready for review.':'Jedno jasno mesto za ono što zahteva pažnju, ko je odgovoran i šta je spremno za pregled.','Open work queue':'Otvori radnu listu','Finance centre':'Finansijski centar','Suppliers, payables, cash, forecast, approvals and period close are managed in OpsNest Desktop today.':'Dobavljači, obaveze, kasa, prognoza, odobravanja i zaključavanje perioda danas se vode u OpsNest Desktop aplikaciji.','See finance readiness':'Pogledaj spremnost finansija','Document control':'Kontrola dokumenata','Private document intake is ready to activate when your EU storage policy is chosen.':'Privatni prijem dokumenata je spreman za aktivaciju kada izaberete EU politiku skladištenja.','Open Document Inbox':'Otvori prijem dokumenata','Team continuity':'Kontinuitet tima','Roles, password recovery, task assignment and control trail keep work moving when one person is away.':'Uloge, oporavak lozinke, dodela zadataka i kontrolni trag održavaju rad kada je neko odsutan.','Open team controls':'Otvori kontrole tima','One controlled business flow':'Jedan kontrolisan poslovni tok','Every stage is visible to the owner and ready for the accountant. External connectors are activated only when your company enables them.':'Svaka faza je vidljiva vlasniku i spremna za knjigovođu. Spoljni konektori se aktiviraju tek kada ih vaša firma uključi.','Project, contract and budget':'Projekat, ugovor i budžet','Invoice, receipt or contract':'Faktura, račun ili ugovor','Responsible person and owner control':'Odgovorna osoba i kontrola vlasnika','Cash, bank and payable control':'Kasa, banka i kontrola obaveza','Reports, audit and local tax module':'Izveštaji, audit i lokalni poreski modul'});
function tr(value){const source=String(value??'');return language==='sr'?(SR_TRANSLATIONS[source]||SR_DASHBOARD_TRANSLATIONS[source]||source):source;}
function localizePortal(){document.documentElement.lang=language==='sr'?'sr':'en';document.title=language==='sr'?'OpsNest radni prostor':'OpsNest Workspace';document.querySelectorAll('body *').forEach(element=>{for(const node of element.childNodes){if(node.nodeType!==3)continue;if(node.__opsnestBase===undefined)node.__opsnestBase=node.nodeValue;node.nodeValue=tr(node.__opsnestBase);}if(element.hasAttribute('placeholder')){if(!element.dataset.opsnestPlaceholder)element.dataset.opsnestPlaceholder=element.getAttribute('placeholder')||'';element.setAttribute('placeholder',tr(element.dataset.opsnestPlaceholder));}});}
const $=id=>document.getElementById(id),saved=()=>JSON.parse(sessionStorage.getItem('opsnestWorkspaceSession')||'null');let session=saved();
const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const headers=()=>({'Content-Type':'application/json','Authorization':'Bearer '+session.member_token,'X-OpsNest-Workspace':session.workspace_id,'X-OpsNest-Member':session.member_id});
async function api(path,options={}){const response=await fetch(path,{...options,headers:{...headers(),...(options.headers||{})}});const body=await response.json().catch(()=>({}));if(!response.ok)throw Error(tr(body.detail||'The workspace request could not be completed.'));return body;}
let workflowItems={},workflowCanManage=false,teamCanManage=false,currentMemberId='';
const workflowLabel={document:'Document check',payment:'Payment',vat:'VAT control',review:'Review',other:'Other',open:'Open',in_progress:'In progress',waiting:'Waiting',done:'Done',low:'Low',normal:'Normal',high:'High',urgent:'Urgent'};
function workflowOptions(selected){return ['open','in_progress','waiting','done'].map(value=>`<option value="${value}" ${value===selected?'selected':''}>${workflowLabel[value]}</option>`).join('');}
function renderWorkflow(data){workflowItems=Object.fromEntries(data.items.map(item=>[item.id,item]));workflowCanManage=Boolean(data.can_manage);$('queueForm').classList.toggle('hidden',!workflowCanManage);$('queueAssignee').innerHTML='<option value="">Unassigned</option>'+data.members.map(member=>`<option value="${esc(member.id)}">${esc(member.display_name||member.email)}</option>`).join('');$('documentWorkflow').innerHTML='<option value="">No work item</option>'+data.items.map(item=>`<option value="${esc(item.id)}">${esc(item.title)}</option>`).join('');if(!data.items.length){$('workflowList').innerHTML='<p class="muted">No work items yet. Start with the next payment, document or VAT check.</p>';localizePortal();return;}$('workflowList').innerHTML=data.items.map(item=>{const status=workflowCanManage?`<select class="workflow-status" data-id="${esc(item.id)}">${workflowOptions(item.status)}</select>`:`<span class="status">${esc(workflowLabel[item.status]||item.status)}</span>`;const due=item.due_date?` · ${tr('due')} ${esc(item.due_date)}`:'';return `<article class="queue-item"><div><b>${esc(item.title)}</b><p>${esc(workflowLabel[item.workflow_type]||item.workflow_type)} · ${esc(workflowLabel[item.priority]||item.priority)}${due} · ${esc(item.assigned_member_name)}</p></div><div class="queue-actions">${status}<button class="quiet small workflow-comment" data-id="${esc(item.id)}" type="button">Comment (${item.comment_count})</button><button class="quiet small workflow-history" data-id="${esc(item.id)}" type="button">History</button></div></article>`;}).join('');localizePortal();document.querySelectorAll('.workflow-status').forEach(control=>control.addEventListener('change',async event=>{const item=workflowItems[event.target.dataset.id];try{await api('/v1/workflow-items/'+item.id,{method:'PATCH',body:JSON.stringify({status:event.target.value,priority:item.priority,due_date:item.due_date,assigned_member_id:item.assigned_member_id})});await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;}}));document.querySelectorAll('.workflow-comment').forEach(button=>button.addEventListener('click',()=>addWorkflowComment(button.dataset.id)));document.querySelectorAll('.workflow-history').forEach(button=>button.addEventListener('click',()=>showWorkflowHistory(button.dataset.id)));}
async function loadWorkflow(){try{$('queueStatus').textContent='';renderWorkflow(await api('/v1/workflow-items'));}catch(error){$('queueStatus').textContent=error.message;}}
async function addWorkflowComment(id){const body=window.prompt('Add an operational comment. Do not enter passwords, invoice files or payment credentials.');if(!body||!body.trim())return;try{await api('/v1/workflow-items/'+id+'/comments',{method:'POST',body:JSON.stringify({body:body.trim()})});await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;}}
async function showWorkflowHistory(id){try{const data=await api('/v1/workflow-items/'+id+'/comments'),line=String.fromCharCode(10);window.alert(data.comments.length?data.comments.map(comment=>`${comment.created_at.replace('T',' ')} · ${comment.author_name}${line}${comment.body}`).join(line+line):'No comments yet.');}catch(error){$('queueStatus').textContent=error.message;}}
function documentSize(bytes){return bytes<1048576?`${Math.ceil(bytes/1024)} KB`:`${(bytes/1048576).toFixed(1)} MB`;}
function renderDocuments(data){const enabled=Boolean(data.storage.enabled);$('documentForm').classList.toggle('hidden',!enabled);if(!enabled){$('documentStatus').className='muted';$('documentStatus').textContent='Document storage is not enabled yet. No files can be uploaded until the private bucket is configured.';}$('documentList').innerHTML=data.documents.map(document=>`<article class="queue-item"><div><b>${esc(document.original_filename)}</b><p>${esc(document.document_type)} · ${documentSize(document.byte_size)} · ${esc(document.uploaded_by_name)} · ${esc(document.created_at.replace('T',' '))}</p></div><div class="queue-actions"><button class="quiet small download-document" data-id="${esc(document.id)}" type="button">Download</button></div></article>`).join('')||'<p class="muted">No uploaded documents.</p>';localizePortal();document.querySelectorAll('.download-document').forEach(button=>button.addEventListener('click',()=>downloadDocument(button.dataset.id)));}
async function loadDocuments(){try{renderDocuments(await api('/v1/documents'));}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}}
async function downloadDocument(id){try{const data=await api('/v1/documents/'+id+'/download');window.open(data.url,'_blank','noopener');}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}}
function renderTeam(data){$('teamList').innerHTML=data.members.map(member=>{const revoke=member.id!==currentMemberId&&member.role!=='owner'&&member.status!=='revoked'?`<button class="quiet small revoke-member" data-id="${esc(member.id)}" type="button">Revoke access</button>`:'';return `<article class="queue-item"><div><b>${esc(member.display_name||member.email)}</b><p>${esc(member.email)} · ${esc(member.role_label)} · ${esc(member.status)}</p></div><div class="queue-actions">${revoke}</div></article>`;}).join('')||'<p class="muted">No team members yet.</p>';localizePortal();document.querySelectorAll('.revoke-member').forEach(button=>button.addEventListener('click',()=>revokeTeamMember(button.dataset.id)));}
async function loadTeam(){if(!teamCanManage)return;try{$('teamStatus').textContent='';renderTeam(await api('/v1/team/members'));}catch(error){$('teamStatus').textContent=error.message;}}
async function revokeTeamMember(id){if(!window.confirm('Revoke this person’s access immediately? Their active sessions will be signed out.'))return;try{await api('/v1/team/members/'+id+'/revoke',{method:'POST'});await loadTeam();}catch(error){$('teamStatus').textContent=error.message;}}
function auditLabel(action){return String(action||'').replaceAll('_',' ').replaceAll('.',' · ');}
function renderAudit(data){$('auditList').innerHTML=data.events.slice(0,20).map(event=>`<article class="queue-item"><div><b>${esc(auditLabel(event.action))}</b><p>${esc(event.at.replace('T',' ').slice(0,19))} · ${esc(event.actor_name)}</p></div></article>`).join('')||'<p class="muted">No operational events recorded yet.</p>';localizePortal();}
async function loadAudit(){if(!teamCanManage)return;try{renderAudit(await api('/v1/team/audit'));}catch(error){$('auditList').innerHTML=`<p class="error">${esc(error.message)}</p>`;}}
function showApp(data){$('loginView').classList.add('hidden');$('appView').classList.remove('hidden');$('logout').classList.remove('hidden');currentMemberId=data.member.id;teamCanManage=Boolean(data.team.can_manage);$('teamSection').classList.toggle('hidden',!teamCanManage);$('auditSection').classList.toggle('hidden',!teamCanManage);$('companyName').textContent=data.workspace.company_name||'OpsNest workspace';$('heroCopy').textContent=`${data.member.role_label} • ${data.workspace.country_label} • ${data.workspace.country_pack_stage}`;
 const sync=data.sync.enabled?`Revision ${data.sync.revision}`:'Not connected yet';$('metrics').innerHTML=[['Plan',data.license.effective_plan_code||data.license.plan_code],['Team seats',`${data.team.seats_used} / ${data.team.seat_limit}`],['Cloud sync',sync],['Role',data.member.role_label]].map(([label,value])=>`<article class="card metric"><span>${esc(label)}</span><b>${esc(value)}</b></article>`).join('');
 $('modules').innerHTML=data.modules.map(module=>`<div class="module"><div><b>${esc(module.title)}</b><p>${esc(module.detail)}</p></div><span class="status ${module.state==='foundation'?'foundation':''}">${esc(module.state)}</span></div>`).join('');
 $('countrySummary').textContent=`${data.workspace.country_label} (${data.workspace.country_code}) · ${data.workspace.default_currency}. ${data.workspace.country_pack_stage}.`;
 const canEdit=Boolean(data.team.can_manage);$('profileForm').classList.toggle('hidden',!canEdit);if(canEdit){$('countryCode').value=data.workspace.country_code;$('currency').value=data.workspace.default_currency;$('profile').value=data.workspace.business_profile;}localizePortal();}
async function load(){try{showApp(await api('/v1/workspace/overview'));await Promise.all([loadWorkflow(),loadDocuments(),loadTeam(),loadAudit()]);}catch(error){session=null;sessionStorage.removeItem('opsnestWorkspaceSession');$('loginView').classList.remove('hidden');$('appView').classList.add('hidden');$('logout').classList.add('hidden');$('loginError').textContent=error.message;}}
$('loginForm').addEventListener('submit',async event=>{event.preventDefault();$('loginError').textContent='';try{const response=await fetch('/v1/team/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:$('workspaceId').value.trim(),email:$('email').value.trim(),password:$('password').value,device_name:'OpsNest Workspace Portal'})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Sign-in failed.');session={workspace_id:data.workspace_id,member_id:data.member_id,member_token:data.member_token};sessionStorage.setItem('opsnestWorkspaceSession',JSON.stringify(session));await load();}catch(error){$('loginError').textContent=error.message;}});
$('showReset').addEventListener('click',()=>{$('loginForm').classList.add('hidden');$('resetForm').classList.remove('hidden');$('resetWorkspaceId').value=$('workspaceId').value.trim();$('resetEmail').value=$('email').value.trim();$('resetStatus').textContent='';});
$('backToLogin').addEventListener('click',()=>{$('resetForm').classList.add('hidden');$('loginForm').classList.remove('hidden');$('resetStatus').textContent='';});
$('sendReset').addEventListener('click',async()=>{const workspace_id=$('resetWorkspaceId').value.trim(),email=$('resetEmail').value.trim();$('resetStatus').className='muted';$('resetStatus').textContent='Sending recovery code…';try{const response=await fetch('/v1/team/password-reset/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id,email})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Recovery request could not be completed.');$('resetStatus').className='muted';$('resetStatus').textContent=data.message;}catch(error){$('resetStatus').className='error';$('resetStatus').textContent=error.message;}});
$('resetForm').addEventListener('submit',async event=>{event.preventDefault();$('resetStatus').className='muted';$('resetStatus').textContent='Changing password…';try{const response=await fetch('/v1/team/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:$('resetWorkspaceId').value.trim(),email:$('resetEmail').value.trim(),code:$('resetCode').value.trim(),password:$('resetPassword').value})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Password could not be changed.');$('workspaceId').value=$('resetWorkspaceId').value.trim();$('email').value=$('resetEmail').value.trim();$('password').value='';$('resetForm').classList.add('hidden');$('loginForm').classList.remove('hidden');$('loginError').textContent=data.message;}catch(error){$('resetStatus').className='error';$('resetStatus').textContent=error.message;}});
$('queueForm').addEventListener('submit',async event=>{event.preventDefault();try{await api('/v1/workflow-items',{method:'POST',body:JSON.stringify({title:$('queueTitle').value.trim(),workflow_type:$('queueType').value,priority:$('queuePriority').value,due_date:$('queueDue').value,assigned_member_id:$('queueAssignee').value})});$('queueTitle').value='';$('queueDue').value='';await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;}});
$('documentForm').addEventListener('submit',async event=>{event.preventDefault();const file=$('documentFile').files[0];if(!file)return;const form=new FormData();form.append('file',file);form.append('document_type',$('documentType').value);form.append('workflow_item_id',$('documentWorkflow').value);$('documentStatus').className='muted';$('documentStatus').textContent='Uploading to private document storage…';try{const response=await fetch('/v1/documents',{method:'POST',headers:{'Authorization':'Bearer '+session.member_token,'X-OpsNest-Workspace':session.workspace_id,'X-OpsNest-Member':session.member_id},body:form});const data=await response.json();if(!response.ok)throw Error(data.detail||'Upload failed.');$('documentFile').value='';$('documentStatus').textContent='Document stored securely.';await loadDocuments();}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}});
$('inviteForm').addEventListener('submit',async event=>{event.preventDefault();try{const data=await api('/v1/team/invitations',{method:'POST',body:JSON.stringify({display_name:$('inviteName').value.trim(),email:$('inviteEmail').value.trim(),role:$('inviteRole').value})});$('inviteName').value='';$('inviteEmail').value='';$('teamStatus').className='muted';$('teamStatus').textContent='Invitation sent. It expires '+data.expires_at.replace('T',' ').slice(0,16)+'.';await loadTeam();}catch(error){$('teamStatus').className='error';$('teamStatus').textContent=error.message;}});
$('profileForm').addEventListener('submit',async event=>{event.preventDefault();try{showApp(await api('/v1/workspace/profile',{method:'POST',body:JSON.stringify({country_code:$('countryCode').value,default_currency:$('currency').value, business_profile:$('profile').value})}));}catch(error){alert(error.message);}});
$('[data-scroll="commandCenter"]')&&document.querySelectorAll('[data-scroll]').forEach(button=>button.addEventListener('click',()=>{const target=$(button.dataset.scroll);if(target)target.scrollIntoView({behavior:'smooth',block:'start'});}));
$('language').value=language;$('language').addEventListener('change',event=>{language=event.target.value==='en'?'en':'sr';localStorage.setItem(LANGUAGE_KEY,language);localizePortal();});$('logout').addEventListener('click',()=>{session=null;sessionStorage.removeItem('opsnestWorkspaceSession');$('loginView').classList.remove('hidden');$('appView').classList.add('hidden');$('logout').classList.add('hidden');$('password').value='';localizePortal();});localizePortal();if(session)load();
</script></body></html>"""
