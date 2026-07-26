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
 .status.attention{background:#fff0ed;color:#b42318}.status.watch{background:#fff6df;color:var(--warn)}.finance-note{margin-top:12px;border-left:4px solid var(--mint);background:#f4fbf9;padding:11px 13px;border-radius:0 10px 10px 0;color:var(--muted);font-size:13px}
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
<button type="button" data-scroll="controlBrief">Daily control brief</button>
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
<section id="controlBrief" class="card section queue">
<h2>Daily control brief</h2>
<p class="muted">Automatic exceptions from work ownership, deadlines, country-pack controls and the freshness of the aggregate Desktop finance review. It never creates a payment, posting or tax filing.</p>
<div id="controlBriefList" class="queue-list"></div>
<p id="controlBriefStatus" class="finance-note"></p>
</section>
<section class="card process"><h2>One controlled business flow</h2><p>Every stage is visible to the owner and ready for the accountant. External connectors are activated only when your company enables them.</p><div class="process-steps"><div class="process-step"><b>01 · PLAN</b>Project, contract and budget</div><div class="process-step"><b>02 · DOCUMENT</b>Invoice, receipt or contract</div><div class="process-step"><b>03 · APPROVE</b>Responsible person and owner control</div><div class="process-step"><b>04 · PAY</b>Cash, bank and payable control</div><div class="process-step"><b>05 · CLOSE</b>Reports, audit and local tax module</div></div></section>
<div id="metrics" class="grid">
</div>
<section id="financialOverviewSection" class="card section queue">
<h2>Financial overview</h2>
<p class="muted">A privacy-minimal company summary from OpsNest Desktop. It shows only totals by one currency — never invoices, suppliers, customers, projects or bank rows.</p>
<div id="financialOverview" class="grid">
</div>
<p id="financialOverviewStatus" class="footer"></p>
</section>
<section id="financialControlSection" class="card section queue">
<h2>Finance control board</h2>
<p class="muted">Priorities are calculated only from the synchronized company totals. They are a control prompt, not a payment instruction or a tax filing.</p>
<div id="financialActions" class="queue-list">
</div>
<p id="financialControlStatus" class="finance-note">Synchronize the Desktop financial overview to generate the first control brief.</p>
</section>
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
<section id="sessionsSection" class="card section queue hidden">
<h2>Active devices</h2>
<p class="muted">Review active work devices and immediately revoke a lost or no-longer-needed session.</p>
<p id="sessionsStatus" class="error"></p>
<div id="sessionsList" class="queue-list"></div>
</section>
<section id="auditSection" class="card section queue hidden">
<h2>Control trail</h2>
<p class="muted">Recent operational events for this company. Passwords, invoices, files and payment credentials never appear here.</p>
<p id="auditIntegrity" class="muted">Audit integrity is checked when this control opens.</p>
<button id="exportAuditEvidence" class="quiet small" type="button">Download audit evidence</button>
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
<section id="countryReadinessSection" class="card section queue">
<h2>Country-pack readiness</h2>
<p id="countryReadinessDisclaimer" class="muted">Local controls are loaded after sign-in.</p>
<p id="countryReadinessStatus" class="error"></p>
<div id="countryReadinessList" class="queue-list"></div>
</section>
<p class="footer">OpsNest Workspace shows secure collaboration metadata. Invoices, attachments and accounting records are never rendered in this browser portal without a dedicated country-specific module.</p>
</section>
</main><script>
const LANGUAGE_KEY='opsnestWorkspaceLanguage';let language=localStorage.getItem(LANGUAGE_KEY)||'sr';
const SR_TRANSLATIONS=Object.freeze({
 'Language':'Jezik','Workspace':'Radni prostor','Sign out':'Odjavi se','Secure company access':'Bezbedan pristup firmi','One place for the owner, accountant and team.':'Jedno mesto za vlasnika, knjigovođu i tim.','Use the central team account created in OpsNest Desktop. Financial documents remain protected in the company workspace.':'Koristite centralni timski nalog napravljen u OpsNest Desktop aplikaciji. Finansijski dokumenti ostaju zaštićeni u radnom prostoru firme.','Workspace ID':'ID radnog prostora','UUID from OpsNest Desktop':'UUID iz OpsNest Desktop aplikacije','Business e-mail':'Poslovni e-mail','Password':'Lozinka','Open workspace':'Otvori radni prostor','Forgot password?':'Zaboravili ste lozinku?','We will send a six-digit, one-time code to the business e-mail. Existing sessions will be signed out after the password changes.':'Poslaćemo jednokratni šestocifreni kod na poslovni e-mail. Sve postojeće sesije biće odjavljene nakon promene lozinke.','Recovery code':'Kod za oporavak','New password':'Nova lozinka','Send recovery code':'Pošalji kod za oporavak','Set new password':'Postavi novu lozinku','Back to sign in':'Nazad na prijavu','Connected workspace':'Povezan radni prostor','Your OpsNest workspace':'Vaš OpsNest radni prostor','Loading collaboration controls…':'Učitavanje kontrola saradnje…','Operational work queue':'Operativna radna lista','Assign document checks, payment preparation, VAT controls and reviews. The queue contains only operational metadata — never invoice files.':'Dodelite proveru dokumenata, pripremu plaćanja, PDV kontrole i preglede. Lista sadrži samo operativne podatke — nikada fajlove faktura.','Work item':'Radni zadatak','Example: Verify supplier invoice before payment':'Primer: Proveri ulaznu fakturu pre plaćanja','Type':'Tip','Priority':'Prioritet','Due date':'Rok','Responsible person':'Odgovorna osoba','Add to work queue':'Dodaj u radnu listu','Document check':'Provera dokumenta','Payment':'Plaćanje','VAT control':'PDV kontrola','Review':'Pregled','Other':'Ostalo','Normal':'Normalan','Low':'Nizak','High':'Visok','Urgent':'Hitan','Open':'Otvoren','In progress':'U toku','Waiting':'Na čekanju','Done':'Završen','Unassigned':'Nedodeljeno','Comment':'Komentar','History':'Istorija','No work items yet. Start with the next payment, document or VAT check.':'Još nema radnih zadataka. Počnite od sledećeg plaćanja, dokumenta ili PDV kontrole.','Document Inbox':'Prijem dokumenata','Private PDF, JPEG and PNG files only. The database stores metadata; the file itself stays in the private document bucket.':'Dozvoljeni su samo privatni PDF, JPEG i PNG fajlovi. Baza čuva metapodatke, a sam fajl ostaje u privatnoj arhivi dokumenata.','File':'Fajl','Invoice':'Faktura','Receipt':'Račun','Contract':'Ugovor','Bank statement':'Izvod banke','Link to work item':'Poveži sa radnim zadatkom','No work item':'Bez radnog zadatka','Upload securely':'Bezbedno otpremi','Download':'Preuzmi','No uploaded documents.':'Nema otpremljenih dokumenata.','Company team':'Tim firme','Invite the right role, see access status and revoke access immediately when responsibilities change.':'Pozovite odgovarajuću ulogu, vidite status pristupa i odmah ukinite pristup kada se odgovornosti promene.','Full name':'Ime i prezime','Role':'Uloga','Accountant':'Knjigovođa','Administrator':'Administrator','Project manager':'Menadžer projekta','Operator':'Operater','Send secure invitation':'Pošalji bezbedan poziv','Revoke access':'Ukinite pristup','No team members yet.':'Još nema članova tima.','Control trail':'Kontrolni trag','Recent operational events for this company. Passwords, invoices, files and payment credentials never appear here.':'Najnoviji operativni događaji za ovu firmu. Lozinke, fakture, fajlovi i podaci za plaćanje se ovde nikada ne prikazuju.','No operational events recorded yet.':'Još nema zabeleženih operativnih događaja.','Platform modules':'Moduli platforme','Company country pack':'Nacionalni paket firme','Country code':'Kod države','Serbia':'Srbija','Bulgaria':'Bugarska','Croatia':'Hrvatska','Bosnia and Herzegovina':'Bosna i Hercegovina','Montenegro':'Crna Gora','North Macedonia':'Severna Makedonija','Slovenia':'Slovenija','International':'Međunarodno','Currency':'Valuta','Business profile':'Poslovni profil','General business':'Opšte poslovanje','Construction':'Građevinarstvo','Services':'Usluge','Trade':'Trgovina','Save platform profile':'Sačuvaj profil platforme','OpsNest Workspace shows secure collaboration metadata. Invoices, attachments and accounting records are never rendered in this browser portal without a dedicated country-specific module.':'OpsNest radni prostor prikazuje zaštićene podatke za saradnju. Fakture, prilozi i knjigovodstvena dokumentacija se ne prikazuju u ovom portalu bez posebnog modula za izabranu državu.','Plan':'Paket','Team seats':'Mesta u timu','Cloud sync':'Cloud sinhronizacija','Not connected yet':'Još nije povezano','Revision':'Revizija','Projects and contracts':'Projekti i ugovori','Operational project records stay available in OpsNest Desktop.':'Operativni podaci o projektima ostaju dostupni u OpsNest Desktop aplikaciji.','Assign document checks, payments, VAT controls and reviews with comments and deadlines.':'Dodelite provere dokumenata, plaćanja, PDV kontrole i preglede uz komentare i rokove.','Private PDF/image intake is ready after the EU document-storage bucket is configured.':'Privatni prijem PDF i slika je spreman nakon podešavanja EU arhive dokumenata.','Money and cash-flow':'Novac i novčani tok','Bank, cash and forecasts remain in the controlled desktop workspace.':'Banka, kasa i prognoze ostaju u kontrolisanom Desktop radnom prostoru.','Accountant collaboration':'Saradnja sa knjigovođom','Team roles, access control and audit are active.':'Uloge tima, kontrola pristupa i kontrolni trag su aktivni.','desktop':'desktop','ready':'spremno','configuration_required':'potrebno podešavanje','member':'član','VAT and fiscalization foundation':'Osnova za PDV i fiskalizaciju','Country-pack foundation':'Osnova nacionalnog paketa','E-invoice and VAT foundation':'Osnova za e-fakture i PDV','Document storage is not enabled yet. No files can be uploaded until the private bucket is configured.':'Arhiva dokumenata još nije aktivirana. Fajlovi se ne mogu otpremiti dok se ne podesi privatni prostor za skladištenje.','Uploading to private document storage…':'Otpremanje u privatnu arhivu dokumenata…','Document stored securely.':'Dokument je bezbedno sačuvan.','Invitation sent. It expires ':'Poziv je poslat. Ističe ','Add an operational comment. Do not enter passwords, invoice files or payment credentials.':'Dodajte operativni komentar. Ne unosite lozinke, fajlove faktura niti podatke za plaćanje.','No comments yet.':'Još nema komentara.','Revoke this person’s access immediately? Their active sessions will be signed out.':'Odmah ukinuti pristup ovoj osobi? Njene aktivne sesije biće odjavljene.','Sending recovery code…':'Slanje koda za oporavak…','Changing password…':'Promena lozinke…','Sign-in failed.':'Prijava nije uspela.','The workspace request could not be completed.':'Zahtev radnog prostora nije mogao da se izvrši.','Recovery request could not be completed.':'Zahtev za oporavak nije mogao da se izvrši.','Password could not be changed.':'Lozinka nije mogla da se promeni.','Upload failed.':'Otpremanje nije uspelo.','E-mail or password is not correct.':'E-mail ili lozinka nisu ispravni.','Invalid or expired team session.':'Nevažeća ili istekla sesija tima.','Wait one minute before requesting another recovery code.':'Sačekajte jedan minut pre nego što zatražite novi kod za oporavak.','Recovery code expired. Request a new code.':'Kod za oporavak je istekao. Zatražite novi kod.','Too many attempts. Request a new recovery code.':'Previše pokušaja. Zatražite novi kod za oporavak.','Recovery code is not correct.':'Kod za oporavak nije ispravan.','Password reset e-mail is not configured yet.':'E-mail za obnovu lozinke još nije podešen.','Owner / Administrator':'Vlasnik / administrator','due':'rok'
});
const SR_DASHBOARD_TRANSLATIONS=Object.freeze({'Overview':'Pregled','Work and approvals':'Rad i odobravanja','Documents':'Dokumenti','Team':'Tim','Controls':'Kontrole','Platform roadmap':'Plan razvoja platforme','Owner command center':'Komandni centar vlasnika','One clear place for what needs attention, who owns it and what is ready for review.':'Jedno jasno mesto za ono što zahteva pažnju, ko je odgovoran i šta je spremno za pregled.','Open work queue':'Otvori radnu listu','Finance centre':'Finansijski centar','Suppliers, payables, cash, forecast, approvals and period close are managed in OpsNest Desktop today.':'Dobavljači, obaveze, kasa, prognoza, odobravanja i zaključavanje perioda danas se vode u OpsNest Desktop aplikaciji.','See finance readiness':'Pogledaj spremnost finansija','Document control':'Kontrola dokumenata','Private document intake is ready to activate when your EU storage policy is chosen.':'Privatni prijem dokumenata je spreman za aktivaciju kada izaberete EU politiku skladištenja.','Open Document Inbox':'Otvori prijem dokumenata','Team continuity':'Kontinuitet tima','Roles, password recovery, task assignment and control trail keep work moving when one person is away.':'Uloge, oporavak lozinke, dodela zadataka i kontrolni trag održavaju rad kada je neko odsutan.','Open team controls':'Otvori kontrole tima','One controlled business flow':'Jedan kontrolisan poslovni tok','Every stage is visible to the owner and ready for the accountant. External connectors are activated only when your company enables them.':'Svaka faza je vidljiva vlasniku i spremna za knjigovođu. Spoljni konektori se aktiviraju tek kada ih vaša firma uključi.','Project, contract and budget':'Projekat, ugovor i budžet','Invoice, receipt or contract':'Faktura, račun ili ugovor','Responsible person and owner control':'Odgovorna osoba i kontrola vlasnika','Cash, bank and payable control':'Kasa, banka i kontrola obaveza','Reports, audit and local tax module':'Izveštaji, audit i lokalni poreski modul','Financial overview':'Finansijski pregled','A privacy-minimal company summary from OpsNest Desktop. It shows only totals by one currency — never invoices, suppliers, customers, projects or bank rows.':'Minimalni zaštićeni zbirni pregled iz OpsNest Desktop aplikacije. Prikazuje samo zbirne iznose u jednoj valuti — nikada fakture, dobavljače, kupce, projekte niti bankovne stavke.','No Desktop financial summary has been synchronized yet.':'Zbirni finansijski pregled iz Desktop aplikacije još nije sinhronizovan.','Income without VAT':'Prihod bez PDV-a','Expenses without VAT':'Rashodi bez PDV-a','Net result':'Neto rezultat','Open receivables':'Otvorena potraživanja','Open payables':'Otvorene obaveze','Forecast closing':'Procena na kraju perioda','Desktop summary is ready.':'Desktop pregled je spreman.','Updated':'Ažurirano'});
const SR_CONTROL_TRANSLATIONS=Object.freeze({'Finance control board':'Finansijska kontrolna tabla','Priorities are calculated only from the synchronized company totals. They are a control prompt, not a payment instruction or a tax filing.':'Prioriteti se računaju samo iz sinhronizovanih zbirnih podataka firme. Oni su kontrolni podsetnik, a ne nalog za plaćanje niti poreska prijava.','Synchronize the Desktop financial overview to generate the first control brief.':'Sinhronizujte finansijski pregled iz Desktop aplikacije da biste dobili prvi kontrolni pregled.','Overdue collection needs review':'Dospela naplata zahteva proveru','Open supplier obligations need a payment plan':'Otvorene obaveze prema dobavljačima zahtevaju plan plaćanja','Forecast shows a liquidity risk':'Prognoza pokazuje rizik likvidnosti','VAT amount needs period control':'Iznos PDV-a zahteva kontrolu perioda','No aggregate exception detected':'Nema prepoznatog odstupanja u zbirnim podacima','Review open receivables in Desktop, confirm collection dates and create one owner task for every material exception.':'U Desktop aplikaciji proverite otvorena potraživanja, potvrdite rokove naplate i napravite zadatak vlasniku za svako značajno odstupanje.','Review supplier due dates and cash availability before any payment is prepared.':'Pre pripreme bilo kog plaćanja proverite rokove dobavljača i raspoloživost novca.','Compare the forecast with planned commitments before approving new spending.':'Uporedite prognozu sa planiranim obavezama pre odobravanja novih troškova.','Reconcile the period with the accountant before filing or paying any VAT obligation.':'Usaglasite period sa knjigovođom pre prijave ili plaćanja bilo kakve PDV obaveze.','The synchronized totals do not show an urgent control exception. Continue the normal review cycle.':'Sinhronizovani zbirni podaci ne pokazuju hitno kontrolno odstupanje. Nastavite redovan ciklus provere.','Attention':'Pažnja','Watch':'Pratiti','Clear':'Bez odstupanja'});
const SR_COUNTRY_PACK_TRANSLATIONS=Object.freeze({'Country-pack readiness':'Spremnost nacionalnog paketa','Local controls are loaded after sign-in.':'Lokalne kontrole se učitavaju nakon prijave.','Status':'Status','Not started':'Nije započeto','In review':'Na proveri','Ready for activation':'Spremno za aktivaciju','Blocked':'Blokirano','Not applicable':'Nije primenljivo','Save control':'Sačuvaj kontrolu','Control owner':'Nosilac kontrole','No owner assigned':'Nije dodeljen nosilac','Local readiness note':'Napomena o lokalnoj spremnosti','Do not enter passwords, credentials or accounting files here.':'Ovde ne unosite lozinke, pristupne podatke niti knjigovodstvene fajlove.','This is a readiness register, not a legal, tax, fiscalisation or e-invoice compliance declaration.':'Ovo je registar spremnosti, a ne potvrda pravne, poreske, fiskalizacione ili e-faktura usklađenosti.','Local accountant validation':'Potvrda lokalnog knjigovođe','E-invoice readiness review':'Provera spremnosti za e-fakture','SEF connection readiness':'Spremnost SEF veze','E-invoice connection readiness':'Spremnost veze za e-fakture','E-invoice and fiscalisation review':'Provera e-faktura i fiskalizacije','VAT period and export review':'Provera PDV perioda i izvoza','VAT period and ledger review':'Provera PDV perioda i evidencija','Archive and retention policy':'Politika arhive i čuvanja','Active devices':'Aktivni uređaji','Review active work devices and immediately revoke a lost or no-longer-needed session.':'Proverite aktivne radne uređaje i odmah opozovite izgubljenu ili nepotrebnu sesiju.','Revoke device':'Opozovi uređaj','Current device':'Trenutni uređaj','Active device session was not found.':'Aktivna sesija uređaja nije pronađena.','Revoke this device session immediately?':'Odmah opozvati sesiju ovog uređaja?'});
const SR_AUTOMATION_TRANSLATIONS=Object.freeze({'Daily control brief':'Dnevni kontrolni sažetak','Automatic exceptions from work ownership, deadlines, country-pack controls and the freshness of the aggregate Desktop finance review. It never creates a payment, posting or tax filing.':'Automatska odstupanja iz odgovornosti za zadatke, rokova, kontrola nacionalnog paketa i ažurnosti zbirnog finansijskog pregleda iz Desktop aplikacije. Nikada ne kreira plaćanje, knjiženje niti poresku prijavu.','Automatic control brief is loading…':'Automatski kontrolni sažetak se učitava…','Operational prompt only — review the relevant controlled workflow before taking action.':'Samo operativni podsetnik — pre postupanja proverite odgovarajući kontrolisani tok.','No active administrator backup':'Nema aktivnog administratora-zamenika','Assign an active administrator who can continue controlled work if the owner is unavailable.':'Dodelite aktivnog administratora koji može nastaviti kontrolisani rad kada vlasnik nije dostupan.','Returned for correction':'Vraćeno na doradu','Explain what must be corrected. Do not enter passwords, invoice files or payment credentials.':'Opišite šta mora da se ispravi. Ne unosite lozinke, fajlove faktura niti podatke za plaćanje.','A correction comment is required.':'Komentar za doradu je obavezan.','Attention':'Pažnja','Watch':'Pratiti','Clear':'Bez odstupanja'});
const SR_DOCUMENT_ACCESS_TRANSLATIONS=Object.freeze({'Your role does not have access to the document archive.':'Vaša uloga nema pristup arhivi dokumenata.','Too many sign-in attempts. Wait 15 minutes or reset the password.':'Previše pokušaja prijave. Sačekajte 15 minuta ili obnovite lozinku.'});
const SR_DOCUMENT_STORAGE_STATUS_TRANSLATIONS=Object.freeze({'Private document storage is temporarily unavailable. Uploads and download links remain safely blocked.':'Privatna arhiva dokumenata je trenutno nedostupna. Otpremanje i linkovi za preuzimanje ostaju bezbedno blokirani.','Private document storage is ready.':'Privatna arhiva dokumenata je spremna.'});
function tr(value){const source=String(value??'');return language==='sr'?(SR_TRANSLATIONS[source]||SR_DASHBOARD_TRANSLATIONS[source]||SR_CONTROL_TRANSLATIONS[source]||SR_COUNTRY_PACK_TRANSLATIONS[source]||SR_AUTOMATION_TRANSLATIONS[source]||SR_DOCUMENT_ACCESS_TRANSLATIONS[source]||SR_DOCUMENT_STORAGE_STATUS_TRANSLATIONS[source]||(source==='days'?'dana':source)):source;}
function localizePortal(){document.documentElement.lang=language==='sr'?'sr':'en';document.title=language==='sr'?'OpsNest radni prostor':'OpsNest Workspace';document.querySelectorAll('body *').forEach(element=>{for(const node of element.childNodes){if(node.nodeType!==3)continue;if(node.__opsnestBase===undefined)node.__opsnestBase=node.nodeValue;node.nodeValue=tr(node.__opsnestBase);}if(element.hasAttribute('placeholder')){if(!element.dataset.opsnestPlaceholder)element.dataset.opsnestPlaceholder=element.getAttribute('placeholder')||'';element.setAttribute('placeholder',tr(element.dataset.opsnestPlaceholder));}});const auditExport=$('exportAuditEvidence');if(auditExport)auditExport.textContent=language==='sr'?'Preuzmi audit dokaz':'Download audit evidence';}
const $=id=>document.getElementById(id),saved=()=>JSON.parse(sessionStorage.getItem('opsnestWorkspaceSession')||'null');let session=saved();
const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const headers=()=>({'Content-Type':'application/json','Authorization':'Bearer '+session.member_token,'X-OpsNest-Workspace':session.workspace_id,'X-OpsNest-Member':session.member_id});
async function api(path,options={}){const response=await fetch(path,{...options,headers:{...headers(),...(options.headers||{})}});const body=await response.json().catch(()=>({}));if(!response.ok)throw Error(tr(body.detail||'The workspace request could not be completed.'));return body;}
let workflowItems={},workflowCanManage=false,teamCanManage=false,currentMemberId='';
const workflowLabel={document:'Document check',payment:'Payment',vat:'VAT control',review:'Review',other:'Other',open:'Open',in_progress:'In progress',waiting:'Waiting',returned:'Returned for correction',done:'Done',low:'Low',normal:'Normal',high:'High',urgent:'Urgent'};
function workflowOptions(selected){return ['open','in_progress','waiting','returned','done'].map(value=>`<option value="${value}" ${value===selected?'selected':''}>${workflowLabel[value]}</option>`).join('');}
function renderWorkflow(data){workflowItems=Object.fromEntries(data.items.map(item=>[item.id,item]));workflowCanManage=Boolean(data.can_manage);$('queueForm').classList.toggle('hidden',!workflowCanManage);$('queueAssignee').innerHTML='<option value="">Unassigned</option>'+data.members.map(member=>`<option value="${esc(member.id)}">${esc(member.display_name||member.email)}</option>`).join('');$('documentWorkflow').innerHTML='<option value="">No work item</option>'+data.items.map(item=>`<option value="${esc(item.id)}">${esc(item.title)}</option>`).join('');if(!data.items.length){$('workflowList').innerHTML='<p class="muted">No work items yet. Start with the next payment, document or VAT check.</p>';localizePortal();return;}$('workflowList').innerHTML=data.items.map(item=>{const status=workflowCanManage?`<select class="workflow-status" data-id="${esc(item.id)}">${workflowOptions(item.status)}</select>`:`<span class="status">${esc(workflowLabel[item.status]||item.status)}</span>`;const due=item.due_date?` · ${tr('due')} ${esc(item.due_date)}`:'';return `<article class="queue-item"><div><b>${esc(item.title)}</b><p>${esc(workflowLabel[item.workflow_type]||item.workflow_type)} · ${esc(workflowLabel[item.priority]||item.priority)}${due} · ${esc(item.assigned_member_name)}</p></div><div class="queue-actions">${status}<button class="quiet small workflow-comment" data-id="${esc(item.id)}" type="button">Comment (${item.comment_count})</button><button class="quiet small workflow-history" data-id="${esc(item.id)}" type="button">History</button></div></article>`;}).join('');localizePortal();document.querySelectorAll('.workflow-status').forEach(control=>control.addEventListener('change',async event=>{const item=workflowItems[event.target.dataset.id],status=event.target.value;let comment='';if(status==='returned'){comment=window.prompt(tr('Explain what must be corrected. Do not enter passwords, invoice files or payment credentials.'))||'';if(comment.trim().length<3){$('queueStatus').textContent=tr('A correction comment is required.');await loadWorkflow();return;}}try{await api('/v1/workflow-items/'+item.id,{method:'PATCH',body:JSON.stringify({status,priority:item.priority,due_date:item.due_date,assigned_member_id:item.assigned_member_id,comment:comment.trim()})});await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;await loadWorkflow();}}));document.querySelectorAll('.workflow-comment').forEach(button=>button.addEventListener('click',()=>addWorkflowComment(button.dataset.id)));document.querySelectorAll('.workflow-history').forEach(button=>button.addEventListener('click',()=>showWorkflowHistory(button.dataset.id)));}
async function loadWorkflow(){try{$('queueStatus').textContent='';renderWorkflow(await api('/v1/workflow-items'));}catch(error){$('queueStatus').textContent=error.message;}}
async function addWorkflowComment(id){const body=window.prompt('Add an operational comment. Do not enter passwords, invoice files or payment credentials.');if(!body||!body.trim())return;try{await api('/v1/workflow-items/'+id+'/comments',{method:'POST',body:JSON.stringify({body:body.trim()})});await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;}}
async function showWorkflowHistory(id){try{const data=await api('/v1/workflow-items/'+id+'/comments'),line=String.fromCharCode(10);window.alert(data.comments.length?data.comments.map(comment=>`${comment.created_at.replace('T',' ')} · ${comment.author_name}${line}${comment.body}`).join(line+line):'No comments yet.');}catch(error){$('queueStatus').textContent=error.message;}}
function documentSize(bytes){return bytes<1048576?`${Math.ceil(bytes/1024)} KB`:`${(bytes/1048576).toFixed(1)} MB`;}
const documentTypeLabel={invoice:'Invoice',receipt:'Receipt',contract:'Contract',statement:'Bank statement',other:'Other'};
function renderDocuments(data){const storage=data.storage||{},ready=storage.state==='ready'||(storage.state===undefined&&Boolean(storage.enabled)),permissions=data.permissions||{},allowed=Array.isArray(permissions.visible_document_types)?permissions.visible_document_types:[],canUpload=Boolean(permissions.can_upload);$('documentForm').classList.toggle('hidden',!ready||!canUpload);$('documentType').innerHTML=allowed.map(value=>`<option value="${esc(value)}">${esc(documentTypeLabel[value]||value)}</option>`).join('');if(!ready){$('documentStatus').className='muted';$('documentStatus').textContent=storage.state==='unavailable'?'Private document storage is temporarily unavailable. Uploads and download links remain safely blocked.':'Document storage is not enabled yet. No files can be uploaded until the private bucket is configured.';}else if(!canUpload){$('documentStatus').className='muted';$('documentStatus').textContent='Your role does not have access to the document archive.';}else{$('documentStatus').className='muted';$('documentStatus').textContent='Private document storage is ready.';}$('documentList').innerHTML=data.documents.map(document=>`<article class="queue-item"><div><b>${esc(document.original_filename)}</b><p>${esc(document.document_type)} · ${documentSize(document.byte_size)} · ${esc(document.uploaded_by_name)} · ${esc(document.created_at.replace('T',' '))}</p></div><div class="queue-actions"><button class="quiet small download-document" data-id="${esc(document.id)}" type="button">Download</button></div></article>`).join('')||'<p class="muted">No uploaded documents.</p>';localizePortal();document.querySelectorAll('.download-document').forEach(button=>button.addEventListener('click',()=>downloadDocument(button.dataset.id)));}
async function loadDocuments(){try{renderDocuments(await api('/v1/documents'));}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}}
async function downloadDocument(id){try{const data=await api('/v1/documents/'+id+'/download');window.open(data.url,'_blank','noopener');}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}}
function renderTeam(data){$('teamList').innerHTML=data.members.map(member=>{const revoke=member.id!==currentMemberId&&member.role!=='owner'&&member.status!=='revoked'?`<button class="quiet small revoke-member" data-id="${esc(member.id)}" type="button">Revoke access</button>`:'';return `<article class="queue-item"><div><b>${esc(member.display_name||member.email)}</b><p>${esc(member.email)} · ${esc(member.role_label)} · ${esc(member.status)}</p></div><div class="queue-actions">${revoke}</div></article>`;}).join('')||'<p class="muted">No team members yet.</p>';localizePortal();document.querySelectorAll('.revoke-member').forEach(button=>button.addEventListener('click',()=>revokeTeamMember(button.dataset.id)));}
async function loadTeam(){if(!teamCanManage)return;try{$('teamStatus').textContent='';renderTeam(await api('/v1/team/members'));}catch(error){$('teamStatus').textContent=error.message;}}
async function revokeTeamMember(id){if(!window.confirm('Revoke this person’s access immediately? Their active sessions will be signed out.'))return;try{await api('/v1/team/members/'+id+'/revoke',{method:'POST'});await loadTeam();}catch(error){$('teamStatus').textContent=error.message;}}
function renderSessions(data){$('sessionsList').innerHTML=data.sessions.map(item=>`<article class="queue-item"><div><b>${esc(item.device_name)}${item.current?' · '+esc(tr('Current device')):''}</b><p>${esc(item.member_name)} · ${esc(item.last_seen_at.replace('T',' '))}</p></div><div class="queue-actions"><button class="quiet small revoke-session" data-id="${esc(item.id)}" type="button">${esc(tr('Revoke device'))}</button></div></article>`).join('')||'<p class="muted">—</p>';document.querySelectorAll('.revoke-session').forEach(button=>button.addEventListener('click',()=>revokeTeamSession(button.dataset.id)));}
async function loadSessions(){if(!teamCanManage)return;try{$('sessionsStatus').textContent='';renderSessions(await api('/v1/team/sessions'));}catch(error){$('sessionsStatus').textContent=error.message;}}
async function revokeTeamSession(id){if(!window.confirm(tr('Revoke this device session immediately?')))return;try{await api('/v1/team/sessions/'+encodeURIComponent(id)+'/revoke',{method:'POST'});await loadSessions();}catch(error){$('sessionsStatus').textContent=error.message;}}
function auditLabel(action){return String(action||'').replaceAll('_',' ').replaceAll('.',' · ');}
function renderAudit(data){$('auditList').innerHTML=data.events.slice(0,20).map(event=>`<article class="queue-item"><div><b>${esc(auditLabel(event.action))}</b><p>${esc(event.at.replace('T',' ').slice(0,19))} · ${esc(event.actor_name)}</p></div></article>`).join('')||'<p class="muted">No operational events recorded yet.</p>';localizePortal();}
async function loadAudit(){if(!teamCanManage)return;try{const [audit,integrity]=await Promise.all([api('/v1/team/audit'),api('/v1/team/audit/integrity')]);renderAudit(audit);$('auditIntegrity').className='muted';$('auditIntegrity').textContent=language==='sr'?`Integritet audit traga je potvrđen · ${integrity.count} događaja.`:`Audit integrity verified · ${integrity.count} events.`;}catch(error){$('auditIntegrity').className='error';$('auditIntegrity').textContent=error.message;$('auditList').innerHTML=`<p class="error">${esc(error.message)}</p>`;}}
async function downloadAuditEvidence(){try{const response=await fetch('/v1/team/audit/evidence.csv',{headers:headers()});if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(data.detail||'The workspace request could not be completed.');}const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='opsnest-audit-evidence.csv';document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);await loadAudit();}catch(error){$('auditIntegrity').className='error';$('auditIntegrity').textContent=tr(error.message);}}
let controlBriefData=null;
function renderControlBrief(data){controlBriefData=data;const labels={attention:'Attention',watch:'Watch',clear:'Clear'},items=Array.isArray(data.items)?data.items:[];$('controlBriefList').innerHTML=items.map(item=>{const title=language==='sr'?item.title_sr:item.title,detail=language==='sr'?item.detail_sr:item.detail,open=item.target?`<button class="quiet small brief-open" data-target="${esc(item.target)}" type="button">${language==='sr'?'Otvori':'Open'}</button>`:'';return `<article class="queue-item"><div><b>${esc(title)}</b><p>${esc(detail)}</p></div><div class="queue-actions"><span class="status ${esc(item.severity)}">${esc(tr(labels[item.severity]||'Watch'))}</span>${open}</div></article>`;}).join('')||'<p class="muted">—</p>';$('controlBriefStatus').textContent=(language==='sr'?data.disclaimer_sr:data.disclaimer)||tr('Operational prompt only — review the relevant controlled workflow before taking action.');document.querySelectorAll('.brief-open').forEach(button=>button.addEventListener('click',()=>{const target=$(button.dataset.target);if(target)target.scrollIntoView({behavior:'smooth',block:'start'});}));}
async function loadControlBrief(){try{renderControlBrief(await api('/v1/workspace/control-brief'));}catch(error){$('controlBriefStatus').className='error';$('controlBriefStatus').textContent=error.message;}}
function money(value,currency){return new Intl.NumberFormat(language==='sr'?'sr-RS':'en-US',{style:'currency',currency:currency||'EUR',maximumFractionDigits:2}).format(Number(value||0));}
function renderFinancialControl(data){const summary=data.summary;if(!summary){$('financialActions').innerHTML='<p class="muted">—</p>';$('financialControlStatus').textContent=tr('Synchronize the Desktop financial overview to generate the first control brief.');localizePortal();return;}const currency=data.currency||summary.currency||'EUR',actions=[],positive=value=>Number(value||0)>0;if(positive(summary.overdue_receivables))actions.push(['Overdue collection needs review',`Open overdue receivables: ${money(summary.overdue_receivables,currency)}. Review open receivables in Desktop, confirm collection dates and create one owner task for every material exception.`,'Attention','attention']);if(positive(summary.open_payables))actions.push(['Open supplier obligations need a payment plan',`Open payables: ${money(summary.open_payables,currency)}. Review supplier due dates and cash availability before any payment is prepared.`,'Watch','watch']);if(Number(summary.forecast_closing||0)<0)actions.push(['Forecast shows a liquidity risk',`Forecast closing: ${money(summary.forecast_closing,currency)}. Compare the forecast with planned commitments before approving new spending.`,'Attention','attention']);if(positive(summary.vat_payable))actions.push(['VAT amount needs period control',`VAT payable: ${money(summary.vat_payable,currency)}. Reconcile the period with the accountant before filing or paying any VAT obligation.`,'Watch','watch']);if(!actions.length)actions.push(['No aggregate exception detected','The synchronized totals do not show an urgent control exception. Continue the normal review cycle.','Clear','']);$('financialActions').innerHTML=actions.map(([title,detail,status,style])=>`<article class="queue-item"><div><b>${esc(title)}</b><p>${esc(detail)}</p></div><span class="status ${style}">${esc(status)}</span></article>`).join('');$('financialControlStatus').textContent=`${tr('Updated')} ${String(data.updated_at||'').replace('T',' ')} · ${summary.horizon_days||data.horizon_days||90} ${tr('days')}.`;localizePortal();}
function renderFinancialOverview(data){const summary=data.summary;if(!summary){$('financialOverview').innerHTML='<article class="card metric"><span>Financial overview</span><b>—</b></article>';$('financialOverviewStatus').textContent=tr(data.message||'No Desktop financial summary has been synchronized yet.');renderFinancialControl(data);localizePortal();return;}const currency=data.currency||summary.currency||'EUR';$('financialOverview').innerHTML=[['Income without VAT',summary.income_net],['Expenses without VAT',summary.expense_net],['Net result',summary.profit_net],['Open receivables',summary.open_receivables],['Open payables',summary.open_payables],['Forecast closing',summary.forecast_closing]].map(([label,value])=>`<article class="card metric"><span>${esc(label)}</span><b>${esc(money(value,currency))}</b></article>`).join('');$('financialOverviewStatus').textContent=`${tr('Desktop summary is ready.')} ${tr('Updated')} ${String(data.updated_at||'').replace('T',' ')} · ${summary.horizon_days||data.horizon_days||90} ${tr('days')}.`;renderFinancialControl(data);localizePortal();}
async function loadFinancialOverview(){try{renderFinancialOverview(await api('/v1/workspace/financial-overview'));}catch(error){$('financialOverviewStatus').className='error';$('financialOverviewStatus').textContent=error.message;}}
let countryReadinessCanManage=false;
const countryControlStatus={not_started:'Not started',in_review:'In review',ready:'Ready for activation',blocked:'Blocked',not_applicable:'Not applicable'};
function countryControlOptions(selected){return Object.entries(countryControlStatus).map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${esc(tr(label))}</option>`).join('');}
function renderCountryReadiness(data){countryReadinessCanManage=Boolean(data.can_manage);$('countryReadinessDisclaimer').textContent=language==='sr'?data.disclaimer_sr:data.disclaimer;$('countryReadinessList').innerHTML=data.controls.map(control=>{const title=language==='sr'?control.title_sr:control.title,detail=language==='sr'?control.detail_sr:control.detail,key=esc(control.key),owners='<option value="">'+esc(tr('No owner assigned'))+'</option>'+data.members.map(member=>`<option value="${esc(member.id)}" ${member.id===control.owner_member_id?'selected':''}>${esc(member.display_name||member.email)}</option>`).join('');if(!countryReadinessCanManage)return `<article class="queue-item"><div><b>${esc(title)}</b><p>${esc(detail)}</p><p>${esc(tr(countryControlStatus[control.status]||control.status))}${control.due_date?' · '+esc(tr('Due date'))+' '+esc(control.due_date):''} · ${esc(control.owner_member_name)}</p>${control.note?`<p>${esc(control.note)}</p>`:''}</div></article>`;return `<article class="queue-item country-control" data-key="${key}"><div><b>${esc(title)}</b><p>${esc(detail)}</p></div><div class="profile country-control-form"><label>${esc(tr('Status'))}<select class="country-control-status">${countryControlOptions(control.status)}</select></label><label>${esc(tr('Due date'))}<input class="country-control-due" type="date" value="${esc(control.due_date)}"></label><label>${esc(tr('Control owner'))}<select class="country-control-owner">${owners}</select></label><label class="wide">${esc(tr('Local readiness note'))}<textarea class="country-control-note" maxlength="1000" placeholder="${esc(tr('Do not enter passwords, credentials or accounting files here.'))}">${esc(control.note)}</textarea></label><button class="primary small country-control-save" type="button">${esc(tr('Save control'))}</button></div></article>`;}).join('')||'<p class="muted">—</p>';document.querySelectorAll('.country-control-save').forEach(button=>button.addEventListener('click',async()=>{const card=button.closest('.country-control');try{$('countryReadinessStatus').textContent='';await api('/v1/workspace/country-pack-readiness/'+encodeURIComponent(card.dataset.key),{method:'PUT',body:JSON.stringify({status:card.querySelector('.country-control-status').value,due_date:card.querySelector('.country-control-due').value,owner_member_id:card.querySelector('.country-control-owner').value,note:card.querySelector('.country-control-note').value.trim()})});await loadCountryReadiness();}catch(error){$('countryReadinessStatus').textContent=error.message;}}));}
async function loadCountryReadiness(){try{$('countryReadinessStatus').textContent='';renderCountryReadiness(await api('/v1/workspace/country-pack-readiness'));}catch(error){$('countryReadinessStatus').textContent=error.message;}}
function showApp(data){$('loginView').classList.add('hidden');$('appView').classList.remove('hidden');$('logout').classList.remove('hidden');currentMemberId=data.member.id;teamCanManage=Boolean(data.team.can_manage);$('teamSection').classList.toggle('hidden',!teamCanManage);$('sessionsSection').classList.toggle('hidden',!teamCanManage);$('auditSection').classList.toggle('hidden',!teamCanManage);$('companyName').textContent=data.workspace.company_name||'OpsNest workspace';$('heroCopy').textContent=`${data.member.role_label} • ${data.workspace.country_label} • ${data.workspace.country_pack_stage}`;
 const sync=data.sync.enabled?`Revision ${data.sync.revision}`:'Not connected yet';$('metrics').innerHTML=[['Plan',data.license.effective_plan_code||data.license.plan_code],['Team seats',`${data.team.seats_used} / ${data.team.seat_limit}`],['Cloud sync',sync],['Role',data.member.role_label]].map(([label,value])=>`<article class="card metric"><span>${esc(label)}</span><b>${esc(value)}</b></article>`).join('');
 $('modules').innerHTML=data.modules.map(module=>`<div class="module"><div><b>${esc(module.title)}</b><p>${esc(module.detail)}</p></div><span class="status ${module.state==='foundation'?'foundation':''}">${esc(module.state)}</span></div>`).join('');
 $('countrySummary').textContent=`${data.workspace.country_label} (${data.workspace.country_code}) · ${data.workspace.default_currency}. ${data.workspace.country_pack_stage}.`;
 const canEdit=Boolean(data.team.can_manage);$('profileForm').classList.toggle('hidden',!canEdit);if(canEdit){$('countryCode').value=data.workspace.country_code;$('currency').value=data.workspace.default_currency;$('profile').value=data.workspace.business_profile;}localizePortal();}
async function load(){try{showApp(await api('/v1/workspace/overview'));await Promise.all([loadControlBrief(),loadWorkflow(),loadDocuments(),loadFinancialOverview(),loadCountryReadiness(),loadTeam(),loadSessions(),loadAudit()]);}catch(error){session=null;sessionStorage.removeItem('opsnestWorkspaceSession');$('loginView').classList.remove('hidden');$('appView').classList.add('hidden');$('logout').classList.add('hidden');$('loginError').textContent=error.message;}}
$('loginForm').addEventListener('submit',async event=>{event.preventDefault();$('loginError').textContent='';try{const response=await fetch('/v1/team/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:$('workspaceId').value.trim(),email:$('email').value.trim(),password:$('password').value,device_name:'OpsNest Workspace Portal'})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Sign-in failed.');session={workspace_id:data.workspace_id,member_id:data.member_id,member_token:data.member_token,session_id:data.session_id||''};sessionStorage.setItem('opsnestWorkspaceSession',JSON.stringify(session));await load();}catch(error){$('loginError').textContent=error.message;}});
$('showReset').addEventListener('click',()=>{$('loginForm').classList.add('hidden');$('resetForm').classList.remove('hidden');$('resetWorkspaceId').value=$('workspaceId').value.trim();$('resetEmail').value=$('email').value.trim();$('resetStatus').textContent='';});
$('backToLogin').addEventListener('click',()=>{$('resetForm').classList.add('hidden');$('loginForm').classList.remove('hidden');$('resetStatus').textContent='';});
$('sendReset').addEventListener('click',async()=>{const workspace_id=$('resetWorkspaceId').value.trim(),email=$('resetEmail').value.trim();$('resetStatus').className='muted';$('resetStatus').textContent='Sending recovery code…';try{const response=await fetch('/v1/team/password-reset/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id,email})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Recovery request could not be completed.');$('resetStatus').className='muted';$('resetStatus').textContent=data.message;}catch(error){$('resetStatus').className='error';$('resetStatus').textContent=error.message;}});
$('resetForm').addEventListener('submit',async event=>{event.preventDefault();$('resetStatus').className='muted';$('resetStatus').textContent='Changing password…';try{const response=await fetch('/v1/team/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:$('resetWorkspaceId').value.trim(),email:$('resetEmail').value.trim(),code:$('resetCode').value.trim(),password:$('resetPassword').value})});const data=await response.json();if(!response.ok)throw Error(data.detail||'Password could not be changed.');$('workspaceId').value=$('resetWorkspaceId').value.trim();$('email').value=$('resetEmail').value.trim();$('password').value='';$('resetForm').classList.add('hidden');$('loginForm').classList.remove('hidden');$('loginError').textContent=data.message;}catch(error){$('resetStatus').className='error';$('resetStatus').textContent=error.message;}});
$('queueForm').addEventListener('submit',async event=>{event.preventDefault();try{await api('/v1/workflow-items',{method:'POST',body:JSON.stringify({title:$('queueTitle').value.trim(),workflow_type:$('queueType').value,priority:$('queuePriority').value,due_date:$('queueDue').value,assigned_member_id:$('queueAssignee').value})});$('queueTitle').value='';$('queueDue').value='';await loadWorkflow();}catch(error){$('queueStatus').textContent=error.message;}});
$('documentForm').addEventListener('submit',async event=>{event.preventDefault();const file=$('documentFile').files[0];if(!file)return;const form=new FormData();form.append('file',file);form.append('document_type',$('documentType').value);form.append('workflow_item_id',$('documentWorkflow').value);$('documentStatus').className='muted';$('documentStatus').textContent='Uploading to private document storage…';try{const response=await fetch('/v1/documents',{method:'POST',headers:{'Authorization':'Bearer '+session.member_token,'X-OpsNest-Workspace':session.workspace_id,'X-OpsNest-Member':session.member_id},body:form});const data=await response.json();if(!response.ok)throw Error(data.detail||'Upload failed.');$('documentFile').value='';$('documentStatus').textContent='Document stored securely.';await loadDocuments();}catch(error){$('documentStatus').className='error';$('documentStatus').textContent=error.message;}});
$('inviteForm').addEventListener('submit',async event=>{event.preventDefault();try{const data=await api('/v1/team/invitations',{method:'POST',body:JSON.stringify({display_name:$('inviteName').value.trim(),email:$('inviteEmail').value.trim(),role:$('inviteRole').value})});$('inviteName').value='';$('inviteEmail').value='';$('teamStatus').className='muted';$('teamStatus').textContent='Invitation sent. It expires '+data.expires_at.replace('T',' ').slice(0,16)+'.';await loadTeam();}catch(error){$('teamStatus').className='error';$('teamStatus').textContent=error.message;}});
$('profileForm').addEventListener('submit',async event=>{event.preventDefault();try{showApp(await api('/v1/workspace/profile',{method:'POST',body:JSON.stringify({country_code:$('countryCode').value,default_currency:$('currency').value, business_profile:$('profile').value})}));await loadCountryReadiness();}catch(error){alert(error.message);}});
$('exportAuditEvidence').addEventListener('click',downloadAuditEvidence);
document.querySelectorAll('[data-scroll]').forEach(button=>button.addEventListener('click',()=>{const target=$(button.dataset.scroll);if(target)target.scrollIntoView({behavior:'smooth',block:'start'});}));
$('language').value=language;$('language').addEventListener('change',event=>{language=event.target.value==='en'?'en':'sr';localStorage.setItem(LANGUAGE_KEY,language);localizePortal();if(controlBriefData)renderControlBrief(controlBriefData);});$('logout').addEventListener('click',()=>{session=null;sessionStorage.removeItem('opsnestWorkspaceSession');$('loginView').classList.remove('hidden');$('appView').classList.add('hidden');$('logout').classList.add('hidden');$('password').value='';localizePortal();});localizePortal();if(session)load();
</script></body></html>"""
