'use strict';
// All data comes from the server's role-filtered API. Preview is a separate dataset.
window.KerblineTraffic = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => value == null ? 'Unknown' : new Intl.NumberFormat('en-AU',{maximumFractionDigits:3}).format(value);
  const cost = value => value == null ? 'Unknown' : `A$${value.toFixed(value===0?3:4)}`;
  const time = value => value ? new Date(value).toLocaleTimeString('en-AU',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not observed';
  const badge = (value,tone='') => `<span class="badge ${tone}">${esc(value)}</span>`;
  const action = (name,label,extra='') => `<button type="button" class="button secondary" data-telemetry="${name}" ${extra}>${esc(label)}</button>`;
  const titles = {BBox_VLM_Crop:'Crop extraction',Code_Mode_Gen:'Code generation',Repeat_Vendor_Fast_Path:'Vendor template',Exception_Summary:'Exception summary',Other:'Other'};
  const tones = {BLOCKED:'danger',ERROR:'danger',NEEDS_APPROVAL:'warning',COMPLETED:'success',RUNNING:'info'};
  let api, changed, accessLabel='', identity='', security=false, mode=null, dataset='live', page=null, selected=null, selectedHop=null;
  let query='', outcome='all', cursor=0, history=[], paused=false, loading=false, fetchError='', generation=0;
  let requestInFlight=false;
  function connect(context) {
    api=context.api; changed=context.onChange; security=context.security; accessLabel=context.accessLabel;
    if(identity!==context.identity){identity=context.identity;reset();}
  }
  function reset(){generation++;page=null;selected=null;selectedHop=null;mode=null;query='';outcome='all';cursor=0;history=[];fetchError='';loading=false;requestInFlight=false;dataset='live';}
  function deactivate(){if(mode){generation++;mode=null;loading=false;requestInFlight=false;}}
  async function refresh(background=false){
    if(!api||!mode||requestInFlight)return;
    const current=++generation;requestInFlight=true;loading=!background;
    try{
      const params=new URLSearchParams({dataset,cursor:String(cursor),limit:'25',q:query,outcome});
      const result=await api(`/api/traces?${params}`);
      if(current!==generation)return;
      page=result;fetchError='';
      if(!page.traces.some(t=>t.traceId===selected)) {selected=page.traces[0]?.traceId||null;selectedHop=null;}
    }catch(e){if(current===generation)fetchError=e.message;}
    finally{if(current===generation){loading=false;requestInFlight=false;if(mode)changed();}}
  }
  function show(kind){
    if(mode!==kind){mode=kind;Promise.resolve().then(()=>refresh());}
    const traces=page?.traces||[], trace=traces.find(t=>t.traceId===selected);
    const tokens=traces.reduce((sum,t)=>sum+(t.summary.totalTokens??0),0);
    const knownCost=traces.reduce((sum,t)=>sum+(t.summary.estimatedModelCostAud??0),0);
    const unknownTokens=traces.filter(t=>t.summary.totalTokens==null).length;
    const unknownCost=traces.filter(t=>t.summary.estimatedModelCostAud==null).length;
    const preview=dataset==='preview';
    const cards=kind==='finance'?
      [['Recorded tokens',!traces.length?'No data':traces.every(t=>t.summary.totalTokens==null)?'Unknown':number(tokens),`${unknownTokens} traces with unknown totals`],['Known model cost',!traces.length?'No data':traces.every(t=>t.summary.estimatedModelCostAud==null)?'Unknown':cost(knownCost),`${unknownCost} traces with unknown cost · estimate`],['Zero-token fast paths',traces.filter(t=>t.summary.fastPath).length,'Count on this page, not a portfolio claim'],['Invoice traces',page?number(page.total):'—','Matching traces · 25 per page']]:
      [['Observed hops',number(traces.reduce((n,t)=>n+(t.hops?.length||0),0)),'On this page · producer-reported'],['Blocked traces',traces.filter(t=>t.outcome==='BLOCKED').length,'Policy outcomes on this page'],['Inference calls',number(traces.reduce((n,t)=>n+(t.hops||[]).filter(h=>h.hopType==='OUTBOUND_INFERENCE'&&h.networkAttempted===true).length,0)),'Known network attempts on this page'],['Telemetry coverage',traces.length?`${traces.filter(t=>t.summary.complete).length} / ${traces.length}`:'—','Finalized snapshots on this page']];
    return `<div class="page-heading telemetry-heading"><div><div class="kicker">${kind==='finance'?'FINOPS & AUD LEDGER':'CONNECTIONS & POLICIES'}</div><h1>${kind==='finance'?'Every prompt. Every model dollar.':'Gateway ↔ customer infrastructure'}</h1><p>${kind==='finance'?'Trace invoice-level usage back to each model call. Cache and reasoning tokens are shown as subsets.':'Follow the request, inspect each boundary, and see exactly where execution stopped.'}</p></div><div class="telemetry-identity">${badge(kind==='finance'?'Finance workspace':'Security workspace','info')}<span>${esc(accessLabel)} · role checks active</span></div></div>
      <div class="telemetry-controls"><div class="dataset-switch" role="group" aria-label="Telemetry dataset"><button data-telemetry="live" class="${!preview?'selected':''}" aria-pressed="${!preview}"><span class="signal-dot"></span>Received traffic</button><button data-telemetry="preview" class="${preview?'selected':''}" aria-pressed="${preview}">Illustrated preview</button></div><div class="stream-state ${fetchError?'stream-error':''}" role="status" aria-live="polite">${preview?'SYNTHETIC DATA':fetchError?'REFRESH FAILED':paused?'POLLING PAUSED':loading?'CONNECTING':page?'POLLING EVERY 5s':'NOT CONNECTED'}<span>${page?`Fetched ${time(page.fetchedAt)}`:'Awaiting collector response'}</span></div>${!preview?action('pause',paused?'Resume polling':'Pause polling'):''}</div>
      ${preview?'<div class="preview-ribbon"><strong>Illustrated preview</strong><span>Synthetic scenarios, example connection metadata and illustrative prices. Excluded from received traffic and evidence.</span></div>':''}
      ${fetchError?`<div class="callout error">${esc(fetchError)} ${page?'Showing the last successful response; it may be stale.':''}</div>`:''}
      <div class="cards telemetry-cards">${cards.map(([label,value,note])=>`<div class="card"><div class="metric-label">${label}</div><div class="metric">${esc(value)}</div><div class="metric-note">${note}</div></div>`).join('')}</div>
      ${kind==='security'?boundary(trace,preview):''}
      <div class="trace-workspace"><section class="panel trace-list-panel"><div class="panel-head"><div><h2>${kind==='finance'?'Invoice usage ledger':'Request explorer'}</h2><p>${preview?'Three synthetic scenarios':'Received snapshots · newest first'}</p></div>${badge(preview?'Preview':`${page?.total??0} traces`)}</div>
      <form id="trace-filter" class="trace-filter"><input name="query" type="search" aria-label="Search traces" placeholder="Invoice reference or trace ID" value="${esc(query)}" maxlength="100"><select name="outcome" aria-label="Filter trace outcome">${[['all','All outcomes'],['COMPLETED','Completed'],['NEEDS_APPROVAL','Needs approval'],['BLOCKED','Blocked'],['ERROR','Error'],['RUNNING','Running']].map(([v,l])=>`<option value="${v}" ${v===outcome?'selected':''}>${l}</option>`).join('')}</select><button class="button secondary">Apply</button></form>
      <div class="trace-list">${traces.length?traces.map(t=>`<button class="trace-select ${selected===t.traceId?'selected':''}" data-trace-id="${esc(t.traceId)}" aria-pressed="${selected===t.traceId}"><span class="trace-select-top"><strong>${esc(t.invoiceRef||t.traceId)}</strong>${badge(t.outcome.replaceAll('_',' '),tones[t.outcome])}</span><span class="trace-subtitle">${esc(t.traceId)}</span><span class="trace-numbers"><span>${number(t.summary.totalTokens)} tokens</span><span>${cost(t.summary.estimatedModelCostAud)}</span><span>${time(t.startedAt)}</span></span>${t.summary.fastPath?'<span class="fastpath-label">FAST-PATH · 0 TOKENS</span>':''}</button>`).join(''):`<div class="empty"><div class="empty-symbol">⇄</div><h3>${loading?'Loading traces…':'No matching traffic received'}</h3><p>${preview?'Try another filter.':kind==='finance'?'Connect instrumented workflows to start the invoice usage ledger. No usage or costs have been assumed.':'Connect an instrumented producer to the ingestion endpoint. No traces, costs or tunnel status have been invented.'}</p>${!preview?`<div class="actions">${action('preview','Explore illustrated preview')}</div>`:''}</div>`}</div>
      <div class="trace-pagination">${action('previous','← Newer',history.length?'':'disabled')}<span>${traces.length} on this page</span>${action('next','Older →',page?.nextCursor!=null?'':'disabled')}</div></section>
      <section class="panel trace-detail-panel">${trace?traceDetail(trace,kind,preview):`<div class="empty trace-empty"><div class="empty-symbol">◇</div><h3>Select a request</h3><p>${kind==='finance'?'Per-prompt token and cost details appear here.':'Connection evidence, policy outcomes and token usage appear here.'}</p></div>`}</section></div>
      <p class="telemetry-footnote">${esc(page?.scope||'Missing telemetry remains unknown.')} ${security?'Reported regions and certificates do not independently attest residency or transport enforcement.':''}</p>`;
  }
  function boundary(trace,preview){
    const hops=trace?.hops||[];
    const ingress=hops.find(h=>h.hopType==='INBOUND_CLIENT');
    const infer=hops.filter(h=>h.hopType==='OUTBOUND_INFERENCE');
    const erp=hops.filter(h=>h.hopType==='OUTBOUND_CUSTOMER_INFRA');
    const blocked=trace?.outcome==='BLOCKED';
    return `<section class="boundary-map"><div class="boundary-head"><div><span class="boundary-eyebrow">REQUEST BOUNDARIES</span><h2>${trace?esc(trace.invoiceRef||trace.traceId):'Your request path, as observed.'}</h2></div><span>${preview?'ILLUSTRATION':trace?'PRODUCER OBSERVATIONS':'NO TELEMETRY'}</span></div><div class="boundary-nodes"><div class="boundary-node"><span class="boundary-symbol">▣</span><div><span>CUSTOMER SOURCE</span><h3>${esc(ingress?.targetSystem||'Not observed')}</h3><p>${esc(ingress?.transport?.replaceAll('_',' ')||'Transport unknown')}</p></div></div><div class="boundary-connector" aria-hidden="true">→</div><div class="boundary-node gateway"><span class="boundary-symbol">K</span><div><span>KERBLINE GATEWAY</span><h3>${blocked?'Stopped at fraud gate':trace?.summary.fastPath?'Verified template route':'Governed execution'}</h3><p>${trace?`${hops.length} reported hops`:'Awaiting instrumentation'}</p></div></div><div class="boundary-connector ${blocked?'blocked':''}" aria-hidden="true">${blocked?'⊣':'→'}</div><div class="boundary-node ${blocked?'node-blocked':''}"><span class="boundary-symbol">⬡</span><div><span>CUSTOMER SYSTEM</span><h3>${erp.length?esc(erp[erp.length-1].targetSystem):'Not observed'}</h3><p>${blocked?egressLabel(trace):erp.length?'Inspect read / write operations':'ERP egress unknown'}</p></div></div></div><div class="boundary-footer"><span><i></i>${infer.length?`${infer.length} reported inference hops`:trace?.summary.llmHops===0?'LLM hops: 0':'Inference coverage unknown'}</span><span>Residency: reported, not attested</span><span>Prompt content never displayed</span></div></section>`;
  }
  function egressLabel(t){if(t.summary.zeroErpEgress)return '0 ERP EGRESS · reported coverage';if(t.summary.zeroErpWriteEgress)return '0 ERP WRITE EGRESS · reads may occur';return 'ERP egress not proven';}
  function traceDetail(t,kind,preview){
    const sum=t.summary;
    const blocked=t.outcome==='BLOCKED';
    return `<div class="panel-head"><div><span class="kicker">${preview?'SYNTHETIC TRACE':'TRACE DETAIL'}</span><h2>${esc(t.invoiceRef||t.traceId)}</h2><p class="mono">${esc(t.traceId)}</p></div>${badge(t.outcome.replaceAll('_',' '),tones[t.outcome])}</div>
      ${sum.fastPath?`<div class="trace-verdict fast"><strong>FAST-PATH · 0 TOKENS</strong><span>LLM hops: 0 · Template verified on ${t.templateVerifiedCount} invoices · A$0.000 model cost</span><small>Infrastructure and processing costs are excluded.</small></div>`:blocked?`<div class="trace-verdict blocked"><strong>BLOCKED AT FRAUD GATE</strong><span>${kind==='security'?esc(egressLabel(t)):'Review the policy outcome in your authorised workflow.'}</span></div>`:''}
      <div class="trace-summary"><div><span>Total tokens</span><strong>${number(sum.totalTokens)}</strong></div><div><span>Est. model cost</span><strong>${cost(sum.estimatedModelCostAud)}</strong></div><div><span>Duration</span><strong>${sum.latencyMs==null?'Unknown':`${number(sum.latencyMs)} ms`}</strong></div></div>
      ${kind==='security'?hopTimeline(t):''}
      <div class="detail-section"><div class="detail-section-head"><h3>Per-prompt usage</h3>${badge(`${sum.promptCount} rows`)}</div>${promptTable(t,kind)}</div>
      ${kind==='security'?`<details class="trace-evidence"><summary>Correlation & evidence limits</summary><dl class="details"><dt>Producer</dt><dd>${esc(t.source)}</dd><dt>Audit correlation</dt><dd>${esc(t.auditCorrelationId||'Not supplied')}</dd><dt>SIEM correlation</dt><dd>${esc(t.siemCorrelationId||'Not supplied')}</dd><dt>WORM correlation</dt><dd>${esc(t.wormCorrelationId||'Not supplied — retention not verified')}</dd><dt>Trace scope</dt><dd>${esc(t.scope||'Unknown')}</dd><dt>Coverage</dt><dd>Final: ${t.coverage.complete?'reported complete':'incomplete'} · Model: ${t.coverage.llmComplete?'complete':'incomplete'} · ERP read: ${t.coverage.erpReadComplete?'complete':'incomplete'} · ERP write: ${t.coverage.erpWriteComplete?'complete':'incomplete'}</dd></dl><p class="hint">Correlation IDs are references, not proof of SIEM delivery, immutable storage or regulatory compliance. Raw/tokenised prompt previews are not exposed.</p></details>`:''}`;
  }
  function hopTimeline(t){
    const hops=t.hops||[];
    const selectedEvidence=hops.find(h=>h.hopId===selectedHop);
    return `<div class="detail-section"><div class="detail-section-head"><h3>Connection timeline</h3><span class="label">Select a hop for evidence</span></div><div class="hop-timeline">${hops.map((h,i)=>`<button class="hop-row ${h.status.startsWith('BLOCKED')?'is-blocked':''} ${selectedHop===h.hopId?'is-selected':''}" data-hop-id="${esc(h.hopId)}" aria-expanded="${selectedHop===h.hopId}"><span class="hop-index">${i+1}</span><span class="hop-description"><strong>${esc(h.targetSystem)}</strong><span>${esc(h.hopType.replaceAll('_',' '))} · ${esc(h.operation)}</span></span><span class="hop-location">${esc(h.region||'Region unknown')}</span><span class="hop-time">${h.latencyMs==null?'Unknown':`${number(h.latencyMs)} ms`}</span><span class="hop-state">${badge(h.status.replaceAll('_',' '),h.status.startsWith('BLOCKED')?'danger':h.status==='OK'?'success':'')}</span></button>`).join('')}</div>${selectedEvidence?hopEvidence(selectedEvidence):''}</div>`;
  }
  function hopEvidence(h){return `<div class="hop-evidence"><div class="detail-section-head"><h3>${esc(h.targetSystem)} · evidence</h3>${badge('Producer-reported','info')}</div><dl class="details">${[['Transport',h.transport.replaceAll('_',' ')],['Auth mode',h.authMode.replaceAll('_',' ')],['Tunnel / endpoint alias',h.tunnelOrEndpointId||'Not supplied'],['mTLS SHA-256',h.mtlsCertFingerprint||'Not supplied'],['PII redacted',h.piiRedacted==null?'Unknown':h.piiRedacted?'Reported true; not attested':'Reported false'],['Detokenised fields',number(h.detokenisedFieldCount)],['Network attempted',h.networkAttempted==null?'Unknown':h.networkAttempted?'Yes':'No'],['Policy verdict',h.policyVerdict||'Not supplied']].map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div>`;}
  function promptTable(t,kind){if(!t.prompts.length)return '<div class="empty"><h3>No prompt usage rows supplied</h3><p>Missing rows are not interpreted as zero tokens or zero cost.</p></div>';
    return `<div class="table-wrap prompt-table"><table><thead><tr><th>Stage / model</th><th>Input</th><th>Cached ⊂ input</th><th>Output</th><th>Reasoning ⊂ output</th><th>Total</th><th>Est. AUD</th></tr></thead><tbody>${t.prompts.map(p=>`<tr class="${p.stage==='Repeat_Vendor_Fast_Path'?'zero-token-row':''}"><td><strong>${esc(titles[p.stage]||p.stage)}</strong><p>${esc(p.model||'No model invoked')}</p>${kind==='security'&&p.promptSha256?`<details><summary>Prompt hash</summary><span class="mono">${esc(p.promptSha256)}</span></details>`:''}</td><td>${number(p.promptTokens)}</td><td>${number(p.cachedTokens)}</td><td>${number(p.completionTokens)}</td><td>${number(p.reasoningTokens)}</td><td><strong>${number(p.totalTokens)}</strong></td><td>${cost(p.estimatedCostAud)}<p>${esc(p.rateCardId||'No rate card')}</p></td></tr>`).join('')}</tbody></table></div><p class="hint">Cache and reasoning are subsets, not extra tokens. Costs are model estimates. GPU time: ${t.prompts.every(p=>p.gpuSeconds!=null)?`${number(t.prompts.reduce((n,p)=>n+p.gpuSeconds,0))} s`:'not supplied'}. Infrastructure costs are excluded.</p>`;
  }
  document.addEventListener('click',event=>{
    const tr=event.target.closest('[data-trace-id]');if(tr){selected=tr.dataset.traceId;selectedHop=null;changed();return;}
    const hop=event.target.closest('[data-hop-id]');if(hop){selectedHop=selectedHop===hop.dataset.hopId?null:hop.dataset.hopId;changed();return;}
    const control=event.target.closest('[data-telemetry]');if(!control||control.disabled)return;
    const name=control.dataset.telemetry;
    if(['live','preview'].includes(name)){dataset=name;page=null;selected=null;selectedHop=null;cursor=0;history=[];generation++;requestInFlight=false;refresh();changed();}
    if(name==='pause'){paused=!paused;if(!paused)refresh(true);changed();}
    if(name==='next'&&page?.nextCursor!=null){history.push(cursor);cursor=page.nextCursor;refresh();}
    if(name==='previous'&&history.length){cursor=history.pop();refresh();}
  });
  document.addEventListener('submit',event=>{if(event.target.id!=='trace-filter')return;event.preventDefault();const data=new FormData(event.target);query=String(data.get('query')||'');outcome=String(data.get('outcome')||'all');cursor=0;history=[];generation++;requestInFlight=false;refresh();});
  setInterval(()=>{if(mode&&dataset==='live'&&!paused&&!document.hidden&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))refresh(true);},5000);
  return {connect,show,deactivate,reset};
})();
