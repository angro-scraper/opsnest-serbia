const {test}=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../..');
// Render the real portal without importing the API or opening any company DB.
const html=execFileSync(process.env.PYTHON||'python',['-X','utf8','-c','from opsnest_cloud.workspace_portal import workspace_portal_html; print(workspace_portal_html())'],{cwd:root,encoding:'utf8'});
function portal(t){
  const dom=new JSDOM(html,{url:'https://opsnest.example.test/workspace',runScripts:'outside-only'});
  const w=dom.window;
  w.fetch=()=>{throw Error('Network is forbidden in portal unit tests');};
  w.eval(w.document.querySelector('script').textContent+'\nwindow.stopTestObserver=()=>portalObserver.disconnect();');
  t.after(()=>{w.stopTestObserver();w.close();});
  return w;
}
function setLanguage(w,language){const select=w.document.getElementById('language');select.value=language;select.dispatchEvent(new w.Event('change'));}
test('Founder has localized unlimited seats; ordinary Pro still shows 20',t=>{
  const w=portal(t),d=w.document;
  const data={member:{id:'owner',role_label:'Owner'},team:{can_manage:true,seats_used:1001,seat_limit:null},
    workspace:{company_name:'QA',country_code:'BG',country_label:'Bulgaria',country_pack_stage:'foundation',default_currency:'EUR',business_profile:'general'},
    sync:{enabled:false},modules:[],license:{access_source:'founder',effective_plan_code:'pro',plan_name:'Founder'}};
  w.showApp(data);
  for(const [lang,expected] of [['sr','Neograničeno'],['bg','Неограничено'],['en','Unlimited'],['sr','Neograničeno']]){
    setLanguage(w,lang);
    assert.match(d.getElementById('metrics').textContent,/Founder/);
    assert.ok(d.getElementById('metrics').textContent.includes(expected));
    assert.doesNotMatch(d.getElementById('metrics').textContent,/null|undefined|1001 \/ 20/);
  }
  data.license={access_source:'subscription',plan_name:'Pro',effective_plan_code:'pro'};
  data.team.seats_used=1;data.team.seat_limit=20;
  w.showApp(data);
  assert.match(d.getElementById('metrics').textContent,/1 \/ 20/);
  assert.doesNotMatch(d.getElementById('metrics').textContent,/Founder|Neograničeno/);
});
test('language changes preserve unsaved input, stable codes and reset navigation',t=>{
  const w=portal(t),d=w.document;
  d.getElementById('email').value='qa@example.test';
  d.getElementById('countryCode').value='BG';
  d.getElementById('currency').value='EUR';
  for(const [lang,label] of [['bg','Забравена парола?'],['en','Forgot password?'],['sr','Zaboravili ste lozinku?'],['bg','Забравена парола?']]){
    setLanguage(w,lang);
    assert.equal(d.getElementById('showReset').textContent,label);
    assert.equal(d.getElementById('email').value,'qa@example.test');
    assert.equal(d.getElementById('countryCode').value,'BG');
    assert.equal(d.getElementById('currency').value,'EUR');
  }
  d.getElementById('showReset').click();
  assert.equal(d.getElementById('resetEmail').value,'qa@example.test');
  assert.equal(d.getElementById('resetForm').classList.contains('hidden'),false);
  assert.equal(d.getElementById('sendReset').textContent,'Изпрати код');
  d.getElementById('backToLogin').click();
  assert.equal(d.getElementById('loginForm').classList.contains('hidden'),false);
});
test('dynamic workflow labels translate while names and task text stay verbatim',async t=>{
  const w=portal(t),d=w.document;
  const item={id:'qa',title:'Payment',status:'open',workflow_type:'payment',priority:'high',due_date:'2026-09-10',assigned_member_name:'Review',comment_count:2};
  w.renderWorkflow({can_manage:true,items:[item],members:[{id:'qa-owner',display_name:'Services'}]});
  for(const lang of ['bg','sr','en','bg']){
    setLanguage(w,lang);
    assert.equal(d.querySelector('#workflowList b').textContent,'Payment');
    assert.equal(d.querySelector('#queueAssignee option[value="qa-owner"]').textContent,'Services');
    assert.equal(d.querySelector('#documentWorkflow option[value="qa"]').textContent,'Payment');
    assert.match(d.getElementById('workflowList').textContent,/Review/);
    assert.equal(d.querySelector('.workflow-status').value,'open');
  }
  assert.match(d.getElementById('workflowList').textContent,/Плащане/);
  assert.match(d.querySelector('.workflow-comment').textContent,/Коментар/);
  assert.equal(w.tr('3 active work item(s) have passed their due date.'),'3 активни задачи са с изтекъл срок.');
  d.getElementById('queueStatus').textContent='A correction comment is required.';
  await new Promise(resolve=>w.setTimeout(resolve,0));
  assert.equal(d.getElementById('queueStatus').textContent,'Необходим е коментар за корекцията.');
});
test('documents and controls escape data, and language switching keeps unsaved review notes',t=>{
  const w=portal(t),d=w.document;
  w.renderDocuments({storage:{state:'ready'},permissions:{can_upload:true,visible_document_types:['invoice']},documents:[{id:'qa',original_filename:'<img src=x onerror=alert(1)>.pdf',document_type:'invoice',byte_size:100,uploaded_by_name:'Payment',created_at:'2026-09-10T12:00:00'}]});
  assert.equal(d.querySelector('#documentList img'),null);
  assert.equal(d.querySelector('#documentList b').textContent,'<img src=x onerror=alert(1)>.pdf');
  w.renderCountryReadiness({can_manage:true,disclaimer:'Local accountant validation',disclaimer_sr:'Potvrda lokalnog knjigovođe',members:[],controls:[{key:'qa',title:'Local accountant validation',title_sr:'Potvrda lokalnog knjigovođe',detail:'Agree VAT period controls, ledger review and accountant sign-off.',detail_sr:'Dogovorite PDV kontrole perioda, proveru evidencija i potvrdu knjigovođe.',status:'in_review',due_date:'',owner_member_id:'',note:'Original note'}]});
  d.querySelector('.country-control-note').value='Unsaved correction';
  for(const lang of ['bg','sr','en','bg']){
    setLanguage(w,lang);
    assert.equal(d.querySelector('.country-control-note').value,'Unsaved correction');
    assert.equal(d.querySelector('.country-control-status').value,'in_review');
  }
  assert.match(d.getElementById('countryReadinessList').textContent,/Проверка от местен счетоводител/);
  assert.doesNotMatch(d.getElementById('countryReadinessList').textContent,/Agree VAT|Potvrda/);
});
test('financial exceptions keep amounts and currency while translating their instructions',t=>{
  const w=portal(t),d=w.document;
  w.renderFinancialOverview({currency:'RSD',updated_at:'2026-09-10T12:00:00',summary:{income_net:1200,expense_net:100,profit_net:1100,open_receivables:300,overdue_receivables:300,open_payables:90,forecast_closing:-50,vat_payable:20,horizon_days:30}});
  d.querySelector('.country-control-note')?.remove();
  for(const lang of ['bg','en','sr','bg']){
    setLanguage(w,lang);
    assert.equal(d.querySelectorAll('#financialActions article').length,4);
    assert.match(d.getElementById('financialOverview').textContent,/RSD/);
    assert.equal(d.querySelectorAll('#financialOverview b').length,6);
  }
  assert.doesNotMatch(d.getElementById('financialActions').textContent,/Review|Compare|VAT payable|Open payables/);
  assert.match(d.getElementById('financialActions').textContent,/Проверете сроковете/);
  assert.match(d.getElementById('financialOverviewStatus').textContent,/Обновено/);
});
