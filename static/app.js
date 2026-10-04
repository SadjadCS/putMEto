const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  briefcase: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3M3 12a20 20 0 0 0 18 0M10 12v3h4v-3"/>',
  settings: '<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  up: '<path d="M6 18 18 6M6 6h12v12"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v4h16v-4"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  shield: '<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6z"/><path d="m8 12 3 3 5-5"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4m-2-2h4"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4zM22 2 11 13"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  edit: '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  book: '<path d="m2 8 10-5 10 5-10 5zM6 10v7c3 3 9 3 12 0v-7M22 8v9"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh: '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
  laptop: '<rect x="4" y="3" width="16" height="13" rx="2"/><path d="m4 16-2 4h20l-2-4"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
};
const icon = (name, cls = '') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeURL = (url) => { try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) ? esc(parsed.href) : '#'; } catch { return '#'; } };
const confirmed = (items) => items.filter(item => item.confirmed);
const LINKEDIN_SEARCH_URL = 'https://www.linkedin.com/jobs/search/';
const routes = {overview:['Overview','grid'],assistant:['Assistant','sparkles'],profile:['My profile','file'],preferences:['Job preferences','target'],sources:['Job sources','globe'],jobs:['Discover jobs','search'],applications:['Applications','briefcase'],settings:['Settings','settings']};
let state, currentRoute = 'overview', jobFilter = 'all', jobQuery = '', busyCount = 0, navigationVersion = 0;
const codex = {status:null,chat:{messages:[],pending:[],busy:false},draft:'',error:'',pollError:'',authURL:'',action:false,timer:null,controller:null,generation:0};
const providerDrafts = {};
const app = document.querySelector('#app');
const modal = document.querySelector('#modal');

async function api(path, {method = 'GET', body, signal} = {}) {
  const options = {method, signal, headers: {'Accept':'application/json'}};
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(`/api${path}`, options);
  let data;
  try { data = await response.json(); } catch (error) { if (error.name === 'AbortError') throw error; throw new Error('The local server returned an unexpected response. Please restart the app.'); }
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => `${d.loc?.slice(1).join(' ') || 'Input'}: ${d.msg}`).join('; ') : data.message || 'Something went wrong. Please try again.');
  }
  return data;
}
async function refresh() { state = await api('/state'); render(); }
function toast(message, error = false) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' error' : ''}`;
  element.innerHTML = `${icon(error ? 'info' : 'check')}<span>${esc(message)}</span><button class="icon-btn" aria-label="Dismiss notification">${icon('x')}</button>`;
  element.querySelector('button').onclick = () => element.remove();
  document.querySelector('#toasts').append(element);
  setTimeout(() => element.remove(), error ? 16000 : 7000);
}
async function task(button, work, {close = false, message = ''} = {}) {
  const original = button?.innerHTML;
  if (button) { button.disabled = true; button.innerHTML = `${icon('refresh','spinner')} Working…`; }
  busyCount++;
  try {
    const result = await work();
    if (close) modal.close();
    await refresh();
    if (result?.warning) toast(result.warning, true);
    if (result?.warnings?.length) toast(result.warnings.join(' '), true);
    if (result?.message || message) toast(result?.message || message);
    return result;
  } catch (error) { toast(error.message, true); return null; }
  finally { busyCount--; if (button?.isConnected) { button.disabled = false; button.innerHTML = original; } }
}
function button(label, action, glyph = '', cls = '', extra = '') { return `<button type="button" class="btn ${cls}" data-action="${action}" ${extra}>${glyph ? icon(glyph) : ''}${label}</button>`; }
function link(label, to, glyph = '', cls = '') { return `<a class="btn ${cls}" href="#${to}">${glyph ? icon(glyph) : ''}${label}</a>`; }
function external(label, url, glyph = 'up', cls = '') { return `<a class="btn ${cls}" href="${safeURL(url)}" target="_blank" rel="noopener noreferrer">${icon(glyph)}${esc(label)}</a>`; }
function heading(title, subtitle, actions = '', eyebrow = 'YOUR WORKSPACE') { return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="actions">${actions}</div></div>`; }
function empty(glyph, title, description, action = '') { return `<div class="empty"><div class="empty-icon">${icon(glyph)}</div><h3>${title}</h3><p>${description}</p>${action}</div>`; }
function badge(label, cls = '', glyph = '') { return `<span class="badge ${cls}">${glyph ? icon(glyph) : ''}${esc(label)}</span>`; }
function field(label, name, value = '', {type = 'text', required = false, full = false, placeholder = ''} = {}) { return `<label class="${full ? 'full' : ''}">${label}<input type="${type}" name="${name}" value="${esc(value)}" ${required ? 'required' : ''} placeholder="${esc(placeholder)}" /></label>`; }
function textarea(label, name, value = '', required = false) { return `<label class="full">${label}<textarea name="${name}" ${required ? 'required' : ''}>${esc(value)}</textarea></label>`; }
function formValues(form) { return Object.fromEntries(new FormData(form)); }
function initial(name) { return esc((name || 'You').split(/\s+/).filter(Boolean).slice(0,2).map(w => w[0]).join('').toUpperCase()); }
function dateLabel(date) { if (!date) return ''; const d = new Date(date); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined,{month:'short',day:'numeric'}); }
function steps() { return [
  {label:'Build your profile',description:'Import your CV, then edit anything you like.',done:confirmed(state.items).length > 0,to:'profile'},
  {label:'Find your direction',description:'Confirm your skills and the roles you want.',done:confirmed(state.skills).length > 0 && confirmed(state.positions).length > 0,to:'preferences'},
  {label:'Choose your job sources',description:'Decide where your next opportunity comes from.',done:state.sources.some(s => s.enabled),to:'sources'},
  {label:'Make your first move',description:'Discover a role and tailor your resume.',done:state.jobs.some(j => j.resume),to:'jobs'},
]; }
function render() {
  if (!state) return;
  const composer = document.querySelector('#codex-prompt');
  if (composer) codex.draft = composer.value;
  currentRoute = location.hash.slice(1).split('?')[0] || 'overview';
  if (!routes[currentRoute]) currentRoute = 'overview';
  document.title = `${routes[currentRoute][0]} · PutMeTo`;
  const name = state.profile.name || 'Your workspace';
  const count = state.jobs.filter(j => j.status !== 'archived').length;
  app.innerHTML = `<div class="shell"><div class="mobile-shade" data-action="menu"></div><aside class="sidebar" aria-label="Main navigation"><a class="brand" href="#overview"><span class="brand-symbol">↗</span>putmeto<span style="color:#9aaa7e">.</span></a><div class="brand-sub">A little closer to what's next</div><div class="nav-label">Workspace</div><nav class="nav">${Object.entries(routes).filter(([key]) => key !== 'settings').map(([key,[label,glyph]]) => `<a class="nav-item ${key === currentRoute ? 'active' : ''}" href="#${key}" ${key === currentRoute ? 'aria-current="page"' : ''}>${icon(glyph)}${label}${key === 'jobs' && count ? `<span class="nav-count">${count}</span>` : ''}</a>`).join('')}<div class="nav-divider"></div><a class="nav-item ${currentRoute === 'settings' ? 'active' : ''}" href="#settings">${icon('settings')}Settings</a></nav><div class="sidebar-bottom"><div class="local-card"><strong>${icon('shield')} Your space. Your data.</strong><p>Your workspace is saved on this computer. You're always in control.</p></div><div class="account"><span class="avatar">${initial(state.profile.name)}</span><div class="account-text"><strong>${esc(name.length > 21 ? name.slice(0,20)+'…' : name)}</strong><span>Personal workspace</span></div><a href="#settings" aria-label="Workspace settings">${icon('settings')}</a></div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb"><button class="icon-btn mobile-menu" data-action="menu" aria-label="Toggle navigation">${icon('menu')}</button><span>Workspace</span>${icon('chevron')}<b>${routes[currentRoute][0]}</b></div><div class="top-actions"><span class="local-badge"><span class="dot"></span>Running locally</span><a class="icon-btn" href="#settings" aria-label="Settings">${icon('settings')}</a></div></header><main class="main" id="main" tabindex="-1">${views[currentRoute]()}<footer class="footer"><span>${icon('laptop')} Made for your next chapter.</span><span>One thoughtful application at a time.</span></footer></main></div></div>`;
  startCodexPolling();
  startWatchPolling();
  startMasterPolling();
  startMatchingPolling();
  startGoldPolling();
  startAutoApplyPolling();
  startSkillGroupPolling();
}

function overview() {
  const checklist = steps(), done = checklist.filter(s => s.done).length;
  const first = state.profile.name?.trim().split(' ')[0];
  const recent = state.jobs.filter(j => j.status !== 'archived').sort((a,b) => b.match_score-a.match_score).slice(0,3);
  return `${heading(first ? `Welcome back, ${esc(first)}.` : 'Your next chapter starts here.', 'A little less searching. A little more possibility.', button('Import your CV','import','upload','primary'))}
  <section class="hero"><div class="hero-copy"><div class="hero-tag">${icon('sparkles')} Your experience, new possibilities</div><h2>You bring the potential.<br>Let’s find <em>your place.</em></h2><p>Turn your experience into a thoughtful job search, with a little help from AI and the final say from you.</p>${link(done < 2 ? 'Set up my profile' : 'Explore opportunities',done < 2 ? 'profile' : 'jobs','arrow','primary')}</div><div class="hero-art" aria-hidden="true"><div class="orbit"></div><div class="orbit two"></div><div class="paper"><div class="paper-icon">${icon('file')}</div><div class="paper-line dark"></div><div class="paper-line"></div><div class="paper-line"></div><div class="paper-line short"></div></div><div class="paper front"><div class="paper-icon">${icon('briefcase')}</div><div class="paper-line dark"></div><div class="paper-line"></div><div class="paper-line"></div><div class="paper-line short"></div></div><div class="floating-check">${icon('check')}</div><span class="spark">✧</span><span class="spark two">✦</span></div></section>
  <div class="stats">${[
    ['Confirmed skills',confirmed(state.skills).length,'sparkles','The strengths you bring'],
    ['Jobs discovered',state.jobs.length,'search','Possibilities worth exploring'],
    ['Resumes tailored',state.jobs.filter(j => j.resume).length,'file','Your story, with the right focus'],
    ['Applications sent',state.applications.filter(a => a.status === 'applied').length,'send','Steps toward something new'],
  ].map(([label,value,glyph,note]) => `<div class="stat"><div class="stat-top"><span>${label}</span>${icon(glyph)}</div><div class="stat-number">${value.toString().padStart(2,'0')}</div><div class="stat-bottom">${note}</div></div>`).join('')}</div>
  <div class="columns"><section class="panel"><div class="panel-head"><div><h2>A good place to begin</h2><p>Four small steps. A more personal job search.</p></div><span class="progress-text">${done} of 4 complete</span></div><div class="panel-body"><div class="progress-track"><span style="width:${done*25}%"></span></div><div style="margin-top:15px">${checklist.map((step,index) => `<div class="step ${step.done ? 'done' : ''}"><div class="step-number">${step.done ? icon('check') : `0${index+1}`}</div><div class="step-copy"><h3>${step.label}</h3><p>${step.description}</p></div><a href="#${step.to}" aria-label="${step.label}">${icon('arrow')}</a></div>`).join('')}</div></div></section><div class="right-column"><section class="panel"><div class="panel-head"><h2>On your radar</h2>${link('View all','jobs','arrow','text small')}</div>${recent.length ? `<div class="panel-body">${recent.map(job => `<a href="#jobs" class="job-preview"><div class="company-mark">${initial(job.company)}</div><div><h3>${esc(job.title)}</h3><p class="meta">${esc(job.company)}</p></div><span class="match">${job.match_score}%</span></a>`).join('')}</div>` : empty('search','Your next role is out there.','Once your profile is ready, we’ll help you find opportunities that fit.',link('Explore job sources','sources','arrow','text small'))}</section><div class="tip-card"><div class="tip-tag">${icon('sparkles')} A thoughtful little reminder</div><h3>Your experience is more<br>versatile than you think.</h3><p>AI can help you find the words. You decide which ones tell your story.</p></div></div></div>`;
}
function xyzNote(item) {
  if (!item.xyz_current) return '';
  const missing = (item.xyz_measured || []).filter(measured => !measured).length;
  const hint = missing ? ` ${missing} ${missing === 1 ? 'bullet has' : 'bullets have'} no number yet; add a real one if you have it.` : '';
  return `<details class="xyz-note"><summary>Your resumes use this Google XYZ wording.${hint} Show your original wording</summary><p class="item-description">${esc(item.enhanced || item.original)}</p></details>`;
}
function showXyz(item) {
  openModal('Edit the XYZ wording',`<p class="modal-description">Google’s XYZ format: accomplished X, as measured by Y, by doing Z. This wording is used in every resume; your original stays on your profile. Use only numbers you can stand behind.</p><form id="xyz-form" data-id="${esc(item.id)}"><div class="compare"><div><h3>Your original wording</h3><p>${esc(item.enhanced || item.original)}</p></div><div><h3>XYZ wording</h3><label><span class="sr-only">XYZ wording</span><textarea name="xyz" required>${esc(item.xyz)}</textarea></label></div></div><div class="form-footer"><button class="btn primary" type="submit">${icon('check')}Save XYZ wording</button></div></form>`);
}
function profile() {
  const p = state.profile, pending = state.items.filter(i => !i.confirmed).length;
  return `${heading('The story so far.','Your experience is the foundation of every application.',button('Preview resume','resume','eye') + button('Add experience','add-item','plus','primary'),'YOUR PROFILE')}
  <div class="upload-zone" id="upload-zone"><div class="upload-copy"><div class="empty-icon">${icon('upload')}</div><div><h3>A fresh start from your existing CV</h3><p>Drop your CV here as a PDF. Imported information is confirmed automatically.</p></div></div>${button('Import your CV','import','upload')}</div>
  <section class="panel watch-panel" id="master-resume" aria-live="polite" ${masterContent() ? '' : 'hidden'}>${masterContent()}</section>
  <section class="panel"><div class="panel-head"><h2>The essentials</h2>${badge('Saved locally','','shield')}</div><form id="profile-form" class="form-pad" style="padding-top:7px"><div class="form-grid">${field('Full name','name',p.name,{required:true})}${field('Professional headline','headline',p.headline,{placeholder:'What you do, in a few words'})}${field('Email address','email',p.email,{type:'email'})}${field('Phone number','phone',p.phone,{type:'tel'})}${field('Location','location',p.location)}${field('Portfolio or website','website',p.website,{type:'url',placeholder:'https://…'})}${textarea('Profile summary','summary',p.summary)}</div><div class="form-footer"><span class="hint">These details appear on your resume.</span><button type="submit" class="btn primary">Save details ${icon('check')}</button></div></form></section>
  <section class="section-gap"><div class="section-title"><div><h2>Your experience & projects</h2><p>Your CV entries are confirmed on import. You can edit them anytime.</p></div>${pending ? badge(`${pending} awaiting review`,'amber','clock') : badge('All caught up','','check')}</div><div class="stack">${state.items.length ? state.items.map(item => `<article class="item-card"><div class="item-head"><div class="item-title"><div class="item-kind">${icon(({experience:'briefcase',project:'code',education:'book',publication:'file'})[item.kind])}</div><div><h3>${esc(item.title)}</h3><p class="meta">${esc(item.organization)}${item.start || item.end ? ` · ${esc(item.start)} – ${esc(item.end || 'Present')}` : ''}</p></div></div>${badge(item.confirmed ? 'Confirmed' : 'Needs review',item.confirmed ? '' : 'amber',item.confirmed ? 'check' : 'clock')}</div><p class="item-description">${esc(item.xyz_current ? item.xyz : item.enhanced || item.original)}</p>${xyzNote(item)}<div class="item-footer"><span class="review-note">${item.confirmed ? 'Ready to include in your resume' : 'Review the wording before it joins your profile'}</span><div class="actions">${button('','delete-item','trash','text small',`data-id="${esc(item.id)}" aria-label="Delete ${esc(item.title)}"`)}${button('Edit','edit-item','edit','text small',`data-id="${esc(item.id)}"`)}${item.xyz_current ? button('Edit XYZ','edit-xyz','edit','text small',`data-id="${esc(item.id)}"`) : ''}${button(item.confirmed ? 'Review' : 'Review & confirm','review-item','check',item.confirmed ? 'small' : 'small primary',`data-id="${esc(item.id)}"`)}</div></div></article>`).join('') : `<div class="panel">${empty('briefcase','Your experience belongs here.','Import a CV or add your work, projects, education, and publications yourself.',button('Add your first item','add-item','plus','primary'))}</div>`}</div></section>
  <section class="panel section-gap" id="technical-skills"><div class="panel-head skills-panel-head"><div><h2>Your technical skills</h2><p>Select the skills to use in your resume.</p></div><div class="actions">${button('Import skills from CV','import-skills','upload','small')}${button('Suggest skills','suggest-skills','sparkles','small')}${state.skills.length ? button('Group by context','regroup-skills','grid','small') : ''}</div></div><div class="panel-body"><p class="hint skill-group-status" id="skill-group-status" aria-live="polite">${skillGroupNote()}</p><div id="skill-list">${chips('skills')}</div><form class="inline-form" data-bag="skills"><label class="sr-only" for="add-skill">Add a technical skill</label><input id="add-skill" name="name" placeholder="Add a skill, e.g. Python or data analysis" required maxlength="100"/><button class="btn" type="submit">${icon('plus')}Add skill</button></form>${termBagsPanel()}</div></section>`;
}
function termBagsPanel() {
  const bags = (state.term_bags?.bags || []).map(bag => Array.isArray(bag) ? {names:bag,phrasings:[]} : bag);
  if (!bags.length) return state.items.some(item => item.confirmed) ? `<p class="hint term-bags-note">A bag of similar terms for each of your technical terms, such as LLM and Large Language Model or software development and software engineering, is made in the background once your master CV is ready.</p>` : '';
  const bag = item => `<span class="chip term-bag">${item.names.map(esc).join('<span class="term-equals" aria-label="same as"> = </span>')}${item.phrasings.map(text => `<span class="term-equals" aria-label="also phrased as"> ≈ </span>${esc(text)}`).join('')}</span>`;
  return `<details class="term-bags"><summary>Similar terms for your skills (${bags.length})</summary><p class="hint">Each job’s resume uses the posting’s choice from these bags. Other names (=) are switched in the text, with an abbreviation and its full name written together once, as in “Large Language Models (LLMs)”. Other phrasings (≈) are added to that job’s skills, since swapping them inside a sentence could break it.</p><div class="chip-list">${bags.map(bag).join('')}</div>${button('List again','rebuild-term-bags','refresh','text small')}</details>`;
}
function scrollToSkills() { document.querySelector('#technical-skills')?.scrollIntoView({block:'start'}); }
function reviewSkills() {
  if (location.hash === '#profile?skills') { render(); scrollToSkills(); }
  else location.hash = 'profile?skills';
}
function chip(bag, item) {
  const why = item.reason ? ` title="${esc(`${item.fit === 'possible' ? 'Possible fit' : 'Strong fit'}: ${item.reason}`)}"` : '';
  return `<span class="chip ${item.confirmed ? 'confirmed' : ''} ${item.fit === 'possible' ? 'possible' : ''}" ${bag === 'skills' ? `data-skill-id="${esc(item.id)}"` : ''}${why}><label><input type="checkbox" data-bag="${bag}" data-id="${esc(item.id)}" ${item.confirmed ? 'checked' : ''} aria-label="Confirm ${esc(item.name)}"/><span class="chip-name">${esc(item.name)}</span></label><button data-action="remove-chip" data-bag="${bag}" data-id="${esc(item.id)}" aria-label="${(bag === 'skills' || item.origin === 'suggestion') && !item.confirmed ? 'Dismiss' : 'Remove'} ${esc(item.name)}">${icon('x')}</button></span>`;
}
function chips(bag) {
  if (!state[bag].length) return `<p class="hint">${bag === 'skills' ? 'Add a skill, import your CV, or get suggestions.' : 'Add roles you’re interested in, or let the AI list every title your master resume supports.'}</p>`;
  if (bag === 'positions' && state.positions.some(item => item.family)) return roleGroups();
  if (bag === 'skills' && state.skills.some(item => item.group)) return skillGroups();
  return `<div class="chip-list">${state[bag].map(item => chip(bag,item)).join('')}</div>`;
}
function roleGroups() {
  // Your own roles first, then each career track the master resume supports, strong fits first.
  const yours = state.positions.filter(item => !item.family);
  const families = [...new Set(state.positions.filter(item => item.family).map(item => item.family))];
  const strongFirst = items => [...items].sort((a,b) => (a.fit === 'possible') - (b.fit === 'possible'));
  return `${yours.length ? `<div class="role-group"><h3>Your roles</h3><div class="chip-list">${yours.map(item => chip('positions',item)).join('')}</div></div>` : ''}${families.map(family => `<div class="role-group"><h3>${esc(family)}</h3><div class="chip-list">${strongFirst(state.positions.filter(item => item.family === family)).map(item => chip('positions',item)).join('')}</div></div>`).join('')}`;
}
function skillGroups() {
  // Each context group in the AI's order, Other last; skills still being grouped come after.
  const groups = new Map((state.skill_group_order || []).map(name => [name,[]])), waiting = [];
  for (const skill of state.skills) {
    if (!skill.group) { waiting.push(skill); continue; }
    if (!groups.has(skill.group)) groups.set(skill.group,[]);
    groups.get(skill.group).push(skill);
  }
  const other = groups.get('Other'); groups.delete('Other');
  const sections = [...groups].filter(([,items]) => items.length);
  if (other?.length) sections.push(['Other',other]);
  if (waiting.length) sections.push(['Not grouped yet',waiting]);
  return sections.map(([name,items]) => `<div class="role-group skill-group"><h3>${esc(name)}<span class="group-count">${items.length}</span></h3><div class="chip-list">${items.map(item => chip('skills',item)).join('')}</div></div>`).join('');
}
const grouper = {status:null,timer:null};
function skillGroupNote() { const s = grouper.status; return s?.running || s?.state === 'failed' ? esc(s.message) : ''; }
function startSkillGroupPolling() { clearTimeout(grouper.timer); grouper.timer = null; if (document.querySelector('#skill-list')) pollSkillGroups(); }
async function pollSkillGroups() {
  if (!document.querySelector('#skill-list')) return;
  try {
    const wasRunning = grouper.status?.running;
    grouper.status = await api('/skill-groups');
    // Show the new groups without re-rendering the page you are reading.
    const finished = !grouper.status.running && (wasRunning || (!grouper.status.waiting && state.skills.some(skill => !skill.group)));
    if (finished) { state = await api('/state'); const list = document.querySelector('#skill-list'); if (list) list.innerHTML = chips('skills'); }
    const note = document.querySelector('#skill-group-status'); if (note) note.innerHTML = skillGroupNote();
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (document.querySelector('#skill-list')) grouper.timer = setTimeout(pollSkillGroups,grouper.status?.running ? 3000 : 20000);
}
function rolesProgress() { return master.status?.running ? esc(master.status.message) : ''; }
function preferences() {
  const p = state.preferences;
  return `${heading('Where do you want to go?','Find the roles that fit your experience and your ambitions.','','YOUR DIRECTION')}
  <form id="preferences-form"><div class="section-title"><h2>Choose your path</h2></div><div class="track-options">${[['industry','Industry','Build, create, and grow with a company.','briefcase'],['academia','Academia','Research, teach, and advance your field.','book']].map(([value,title,desc,glyph]) => `<label class="track-card ${p.track === value ? 'selected' : ''}"><input type="radio" name="track" value="${value}" ${p.track === value ? 'checked' : ''}/>${icon(glyph)}<div><h3>${title}</h3><p>${desc}</p></div></label>`).join('')}</div><section class="panel"><div class="form-pad"><div class="form-grid">${field('Preferred location','location',p.location,{full:true,placeholder:'City, country, or leave blank for anywhere'})}<label class="check-label full"><input type="checkbox" name="remote_only" ${p.remote_only ? 'checked' : ''}/> Only show remote opportunities</label></div><div class="form-footer"><button class="btn primary" type="submit">Save preferences ${icon('check')}</button></div></div></section></form>
  <section class="panel section-gap"><div class="panel-head"><div><h2>Roles that feel like you</h2><p>Check a role to include it in your search. Solid outlines are strong fits, dashed ones possible fits; hover a role to see why.</p></div>${button('Suggest roles','suggest-positions','sparkles','small')}</div><div class="panel-body"><p class="hint roles-progress" id="roles-progress" aria-live="polite">${rolesProgress()}</p>${chips('positions')}<form class="inline-form" data-bag="positions"><label class="sr-only" for="add-role">Add a job title</label><input id="add-role" name="name" placeholder="${p.track === 'academia' ? 'e.g. Research Fellow, Postdoctoral Researcher' : 'e.g. Software Engineer, Product Designer'}" required maxlength="100"/><button class="btn" type="submit">${icon('plus')}Add role</button></form></div></section><div class="notice section-gap">${icon('info')}<span>Suggestions are a starting point. Confirm the titles you would actually like to apply for. Your search uses these roles alongside your technical skills.</span></div>`;
}
function sources() {
  const notes = {remotive:'Remote opportunities from Remotive’s public job feed.',greenhouse:'Search the public job board of a company on Greenhouse.',lever:'Explore openings from a company’s Lever career page.',linkedin:'Search LinkedIn for your target positions, in your preferred location or the United States and Europe. Camoufox keeps your LinkedIn sign-in for future searches.',camoufox:'Visit a career page with Camoufox and read JobPosting data.'};
  return `${heading('Good opportunities start somewhere.','Choose the sources you want in your search.',button('Add a source','add-source','plus','primary'),'YOUR JOB SOURCES')}<div class="notice">${icon('globe')}<span>Company boards need a careers URL. LinkedIn opens a visible browser; custom pages need structured job listings. You can also search LinkedIn directly or add any job manually.</span></div><div class="source-grid">${state.sources.map(source => `<article class="source-card ${source.enabled ? 'enabled' : ''}"><div class="source-card-head"><div class="source-logo ${['greenhouse','lever'].includes(source.kind) ? 'green' : ''}">${esc(source.name[0] || 'S')}</div><input class="toggle" type="checkbox" data-source="${esc(source.id)}" ${source.enabled ? 'checked' : ''} aria-label="Enable ${esc(source.name)}"/></div><h3>${esc(source.name)}</h3><p>${notes[source.kind] || notes.camoufox}</p><div class="source-url">${esc(source.url || 'Add a company board URL to get started')}</div>${source.kind === 'linkedin' ? `<div class="actions">${button('Search LinkedIn','search-linkedin','search','small')}</div>` : ''}<div class="item-footer">${badge(['camoufox','linkedin'].includes(source.kind) ? 'Browser discovery' : 'Public job feed','gray',['camoufox','linkedin'].includes(source.kind) ? 'globe' : 'refresh')}<div class="actions">${button('Edit','edit-source','edit','text small',`data-id="${esc(source.id)}"`)}${button('','delete-source','trash','text small',`data-id="${esc(source.id)}" aria-label="Delete ${esc(source.name)}"`)}</div></div></article>`).join('')}</div><div class="section-gap actions">${link('Discover opportunities','jobs','arrow','primary')}<span class="hint">${state.sources.filter(s => s.enabled).length} sources selected for your next search</span></div>`;
}
function jobCard(job) { return `<article class="job-card"><div class="company-mark">${initial(job.company)}</div><div class="job-body"><div class="job-top"><div><h3>${esc(job.title)}</h3><p class="job-company">${esc(job.company)} · ${esc(job.location || 'Location not specified')}</p></div>${atsLabel(job)}</div><div class="job-tags">${badge(job.job_type || 'Opportunity','gray')}${job.salary ? badge(job.salary,'gray') : ''}${badge(job.status || 'discovered',job.status === 'applied' ? '' : 'gray')}${(job.matched_skills || []).slice(0,3).map(s => badge(s)).join('')}</div><div class="job-bottom"><span>via ${esc(job.source)}${posted(job) ? ` · ${esc(posted(job))}` : ''}</span><div class="actions">${job.status === 'discovered' ? button('Save','save-job','bookmark','small',`data-id="${esc(job.id)}"`) : ''}${button('View opportunity','view-job','arrow','small',`data-id="${esc(job.id)}"`)}</div></div></div></article>`; }
const posted = job => dateLabel(job.posted_at) || String(job.posted_at || '').trim(); // LinkedIn gives text such as "1 week ago".
// A full match: the resume as sent has every keyword the posting asks for.
const fullMatch = job => job.ats_score === 100;
function atsLabel(job) {
  if (job.ats_score == null) return `<span class="match pending" title="The ATS keyword check runs in the background.">${icon('search')}ATS not checked yet</span>`;
  const before = job.ats_before ?? job.ats_score;
  return `<span class="match" title="ATS keyword match of the resume as sent${before !== job.ats_score ? `; ${before}% before similar terms and added keywords` : ''}">${icon('search')}ATS ${job.ats_score}%${before !== job.ats_score ? `<span class="match-before">was ${before}%</span>` : ''}</span>`;
}
const atsOrder = job => job.ats_score ?? -1;
function filteredJobs() { return state.jobs.filter(j => (jobFilter === 'all' ? j.status !== 'archived' : jobFilter === 'full' ? fullMatch(j) && j.status !== 'archived' : jobFilter === 'prepared' ? Boolean(j.resume) && j.status !== 'archived' : j.status === jobFilter) && `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(jobQuery.toLowerCase())).sort((a,b) => atsOrder(b)-atsOrder(a) || jobScore(b)-jobScore(a)); }
const jobScore = job => job.ai_match ? job.ai_match.score : Number(job.match_score) || 0;
function jobList() { const jobs = filteredJobs(); return jobs.length ? jobs.map(jobCard).join('') : `<div class="panel">${empty('search',jobQuery ? 'No matches for that search.' : 'Make room for possibility.',jobQuery ? 'Try another title, company, or location.' : 'Confirm your preferred roles, choose your sources, then discover jobs that fit.',jobQuery ? '' : button('Discover jobs','discover','sparkles','primary'))}</div>`; }
function jobs() { return `${heading('A world of possibilities.','Opportunities matched to the experience you’ve confirmed.',button('Add a job','add-job','plus')+button('Search LinkedIn','search-linkedin','search')+button('Discover jobs','discover','sparkles','primary'),'YOUR NEXT MOVE')}<section class="panel watch-panel" id="linkedin-watch" aria-live="polite">${watchContent()}</section><section class="panel watch-panel" id="job-matching" aria-live="polite" ${matchingContent() ? '' : 'hidden'}>${matchingContent()}</section><section class="panel goldmove" id="goldmove" aria-live="polite" ${goldmoveContent() ? '' : 'hidden'}>${goldmoveContent()}</section><div class="toolbar"><div class="tabs" role="group" aria-label="Filter jobs">${[['all','All opportunities'],['full','Full match'],['prepared','Tailored'],['archived','Archived']].map(([key,label]) => `<button class="tab ${jobFilter === key ? 'active' : ''}" data-action="filter-jobs" data-filter="${key}" aria-pressed="${jobFilter === key}">${label}</button>`).join('')}</div><div class="search">${icon('search')}<label class="sr-only" for="job-search">Search jobs</label><input id="job-search" value="${esc(jobQuery)}" placeholder="Search your opportunities…"/></div></div><div class="job-grid" id="job-list">${jobList()}</div><p class="hint section-gap">ATS match: the share of the posting’s keywords your resume contains, with required ones counting double; in red, the match before similar terms and added keywords. An estimate: employers’ own rankings aren’t visible. Always review the full job description and eligibility.</p>`; }
const master = {status:null,timer:null};
function masterContent() {
  const status = master.status;
  if (!status || (!status.has_cv && !status.running && !status.built && !status.xyz_pending)) return '';
  const when = value => new Date(value).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  let title = 'Master resume', note, control = '';
  if (status.running) {
    title = 'Building your master resume'; note = status.message;
  } else if (status.state === 'failed') {
    title = 'Master resume not built'; note = status.message; control = button('Rebuild','master-rebuild','refresh','small primary');
  } else if (status.built) {
    const by = status.built.model ? ` with ${status.built.model}${status.built.effort ? ` at ${status.built.effort} reasoning` : ''}` : '';
    note = `Built from your CV on ${when(status.built.built_at)}${by}, in your own words.`;
    control = status.has_cv ? button('Rebuild','master-rebuild','refresh','small') : '';
  } else if (status.has_cv) {
    note = 'Build a complete, organized master resume from your CV with Codex’s deepest reasoning. Your wording is kept.';
    control = button('Build','master-rebuild','sparkles','small primary');
  } else {
    note = 'Your entries are your master CV.';
  }
  if (!status.running && status.xyz_pending) {
    note += ` ${status.xyz_pending} ${status.xyz_pending === 1 ? 'entry needs' : 'entries need'} Google XYZ wording for your resumes.`;
    control = `<div class="actions">${control}${button('Write XYZ','master-xyz','sparkles','small primary')}</div>`;
  }
  return `<div class="watch-copy"><h2>${icon(status.running ? 'refresh' : 'file',status.running ? 'spinner' : '')}${esc(title)}</h2><p>${esc(note)}</p></div>${control}`;
}
const masterView = () => document.querySelector('#master-resume, #roles-progress');
function renderMaster() {
  const progress = document.querySelector('#roles-progress');
  if (progress) progress.innerHTML = rolesProgress();
  const panel = document.querySelector('#master-resume'); if (!panel) return;
  const content = masterContent(); panel.hidden = !content; panel.innerHTML = content;
}
function startMasterPolling() { clearTimeout(master.timer); master.timer = null; if (masterView()) pollMaster(); }
async function pollMaster() {
  if (!masterView()) return;
  try {
    const wasRunning = master.status?.running;
    master.status = await api('/master-resume');
    // Show the finished master resume's entries; refresh() re-renders and resumes polling.
    if (wasRunning && !master.status.running) { await refresh(); return; }
    renderMaster();
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (masterView()) master.timer = setTimeout(pollMaster,master.status?.running ? 5000 : 60000);
}
const matching = {status:null,timer:null,listedDone:null};
function matchingContent() {
  const status = matching.status;
  if (!status) return '';
  let title, note = status.message, control = '';
  if (status.running) {
    title = 'Matching jobs to your master CV';
  } else if (status.waiting) {
    title = 'Jobs waiting for your master CV';
    note = `${status.waiting} ${status.waiting === 1 ? 'job scores' : 'jobs score'} between 30% and 100% on keywords. Codex can match each one to your master CV, show strengths and gaps, and tailor its resume.`;
    control = button('Match now','match-jobs','sparkles','small primary');
  } else if (note) {
    title = 'Jobs matched to your master CV';
  } else return '';
  return `<div class="watch-copy"><h2>${icon(status.running ? 'refresh' : 'sparkles',status.running ? 'spinner' : '')}${esc(title)}</h2><p>${esc(note)}</p></div>${control}`;
}
function renderMatching() { const panel = document.querySelector('#job-matching'); if (!panel) return; const content = matchingContent(); panel.hidden = !content; panel.innerHTML = content; }
function startMatchingPolling() { clearTimeout(matching.timer); matching.timer = null; if (document.querySelector('#job-matching')) pollMatching(); }
async function pollMatching() {
  if (!document.querySelector('#job-matching')) return;
  try {
    const wasRunning = matching.status?.running;
    matching.status = await api('/job-matching');
    // Show each newly matched job without re-rendering the page the user is reading.
    if ((wasRunning && !matching.status.running) || matching.status.done !== matching.listedDone) {
      matching.listedDone = matching.status.done;
      state = await api('/state');
      const list = document.querySelector('#job-list'); if (list) list.innerHTML = jobList();
    }
    renderMatching();
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (document.querySelector('#job-matching')) matching.timer = setTimeout(pollMatching,matching.status?.running ? 5000 : 30000);
}
const gold = {status:null,timer:null,showAll:false};
function goldmoveContent() {
  const s = gold.status;
  if (!s || (!s.eligible && !s.candidates?.length)) return '';
  const items = s.candidates || [], shown = gold.showAll ? items : items.slice(0,30);
  const progress = s.running ? `${icon('refresh','spinner')} ${esc(s.message)}` : `${s.checked} of ${s.eligible} jobs above 30% checked.`;
  const chip = item => `<label class="chip gold-term" title="${esc(item.jobs.slice(0,6).map(job => `${job.title}${job.company ? ` at ${job.company}` : ''}`).join('\n'))}"><input type="checkbox" name="term" value="${esc(item.term)}" />${esc(item.term)}<span class="group-count">${item.jobs.length} ${item.jobs.length === 1 ? 'job' : 'jobs'}</span>${item.required ? '<b aria-label="required in some jobs">*</b>' : ''}</label>`;
  return `<div class="panel-head"><div><h2>${icon('sparkles')}Goldmove</h2><p>Keywords your jobs above 30% still miss after your CV’s own wording and similar terms are used. Tick the ones you have: they join your skills and every tailored resume not yet sent. Unticked keywords are never added.</p></div></div><div class="panel-body"><p class="hint gold-progress">${progress}</p>${items.length ? `<form id="goldmove-form"><div class="chip-list">${shown.map(chip).join('')}</div>${items.length > 30 ? button(gold.showAll ? 'Show fewer' : `Show all ${items.length}`,'gold-toggle','','text small') : ''}<div class="form-footer"><p class="hint">* Required by at least one job. Hover a keyword to see which jobs ask for it.${s.dismissed ? ` ${button(`Offer ${s.dismissed} dismissed again`,'gold-undismiss','refresh','text small')}` : ''}</p>${button('Select all','gold-select-all','check','small')}${button('Not mine','gold-dismiss','x','small')}<button class="btn small primary" type="submit">${icon('plus')}Add to my skills</button></div></form>` : `<p class="hint">${s.checked ? 'Nothing is missing that you haven’t already decided on.' : ''}</p>`}</div>`;
}
function renderGoldmove(force = false) {
  const panel = document.querySelector('#goldmove'); if (!panel) return;
  // Keep your ticks while you are choosing.
  if (!force && panel.querySelector('input[name="term"]:checked')) { const note = panel.querySelector('.gold-progress'); if (note && gold.status) note.innerHTML = gold.status.running ? `${icon('refresh','spinner')} ${esc(gold.status.message)}` : `${gold.status.checked} of ${gold.status.eligible} jobs above 30% checked.`; return; }
  const content = goldmoveContent(); panel.hidden = !content; panel.innerHTML = content;
}
function startGoldPolling() { clearTimeout(gold.timer); gold.timer = null; if (document.querySelector('#goldmove')) pollGold(); }
async function pollGold() {
  if (!document.querySelector('#goldmove')) return;
  try {
    const scored = gold.status?.scored;
    gold.status = await api('/goldmove'); renderGoldmove();
    if (scored != null && scored !== gold.status.scored) { state = await api('/state'); const list = document.querySelector('#job-list'); if (list) list.innerHTML = jobList(); }
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (document.querySelector('#goldmove')) gold.timer = setTimeout(pollGold,gold.status?.running ? 5000 : 60000);
}
function goldTerms(checkedOnly = true) { return [...document.querySelectorAll(`#goldmove-form input[name="term"]${checkedOnly ? ':checked' : ''}`)].map(input => input.value); }
const watch = {status:null,timer:null,listedFound:null};
function watchContent() {
  const status = watch.status, running = Boolean(status?.running), paused = status?.state === 'paused';
  const time = value => new Date(value).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  const note = status && (running || paused || status.found) ? status.message : 'Searches LinkedIn for your target positions in the background, 20-30 seconds between pages with occasional breaks. New matching jobs appear below.';
  const meta = status && (running || paused || status.found) ? `${status.found} new ${status.found === 1 ? 'job' : 'jobs'} · ${status.pages_last_hour} pages this hour · ${status.pages_today} today${running && status.next_at ? ` · continues around ${time(status.next_at)}` : ''}` : '';
  const control = running ? button('Stop','watch-stop','x','small') : button(paused ? 'Resume' : 'Start','watch-start',paused ? 'refresh' : 'search','small primary');
  return `<div class="watch-copy"><h2>${icon(running ? 'refresh' : 'clock',running ? 'spinner' : '')}Keep searching LinkedIn${paused ? ` ${badge('Paused','amber')}` : ''}</h2><p>${esc(note)}</p>${meta ? `<p class="watch-meta">${esc(meta)}</p>` : ''}</div>${control}`;
}
function renderWatch() { const panel = document.querySelector('#linkedin-watch'); if (panel) panel.innerHTML = watchContent(); }
function startWatchPolling() { clearTimeout(watch.timer); watch.timer = null; if (document.querySelector('#linkedin-watch')) pollWatch(); }
async function pollWatch() {
  if (!document.querySelector('#linkedin-watch')) return;
  try {
    watch.status = await api('/linkedin/continuous');
    if (watch.status.found !== watch.listedFound) {
      // Show newly found jobs without re-rendering the page the user is reading.
      watch.listedFound = watch.status.found;
      if (watch.status.found) { state = await api('/state'); const list = document.querySelector('#job-list'); if (list) list.innerHTML = jobList(); }
    }
    renderWatch();
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (document.querySelector('#linkedin-watch')) watch.timer = setTimeout(pollWatch,watch.status?.running ? 10000 : 30000);
}
const answerFields = [['work_authorization','Work authorization','e.g. Authorized to work in the US'],['sponsorship','Visa sponsorship','e.g. No, I don’t need sponsorship'],['salary','Salary expectation','e.g. $120,000 or open to discussion'],['start_date','Start date or notice','e.g. Two weeks after an offer'],['relocation','Willing to relocate','e.g. Yes, within the US'],['work_arrangement','Work arrangement','e.g. Remote or hybrid'],['years_experience','Years of experience','e.g. 6'],['linkedin_url','LinkedIn profile URL','https://www.linkedin.com/in/…'],['gender','Gender (optional)','Left empty: declined'],['race','Race or ethnicity (optional)','Left empty: declined'],['veteran','Veteran status (optional)','Left empty: declined'],['disability','Disability status (optional)','Left empty: declined']];
const autoApply = {status:null,timer:null,seen:null};
function autoApplyContent() {
  const s = autoApply.status;
  if (!s) return `<div class="watch-copy"><h2>${icon('send')}Apply automatically</h2><p>Checking auto-apply…</p></div>`;
  const on = s.enabled, working = ['applying','checking'].includes(s.state), paused = Boolean(s.blocker) || s.state === 'paused';
  const note = !on ? `Applies to every job with a master-CV match of 70% or more, one at a time, using only your profile, the job’s tailored resume, and your answers below. It submits without asking you, and stops to ask when a form needs something your answers don’t cover.`
    : s.state === 'checking' ? s.message
    : working && s.job ? `Applying to ${s.job.title}${s.job.company ? ` at ${s.job.company}` : ''}${s.step ? ` (step ${s.step})` : ''}.${s.stopping ? ' Stopping after this step.' : ''}`
    : s.blocker || s.message || 'Starting…';
  const meta = on ? [`${s.waiting} ready to apply`,`${s.eligible} at 70% or more`,s.applied ? `${s.applied} submitted since PutMeTo started` : '',s.needs_you ? `${s.needs_you} need you below` : '',s.min_ats && s.below_ats ? `${s.below_ats} below your ATS minimum` : ''].filter(Boolean).join(' · ') : '';
  const control = on ? button(s.stopping ? 'Stopping…' : 'Stop','auto-apply-stop','x','small',s.stopping ? 'disabled' : '') : button('Start','auto-apply-start','send','small primary');
  const browser = s.browser_open ? button('Done signing in','apply-browser-close','check','small primary') : button('Open application browser','apply-browser','globe','small');
  return `<div class="watch-copy"><h2>${icon(working ? 'refresh' : 'send',working ? 'spinner' : '')}Apply automatically${on ? ` ${badge(working ? 'Applying' : paused ? 'Paused' : 'On',paused ? 'amber' : '')}` : ''}</h2><p>${esc(note)}</p>${meta ? `<p class="watch-meta">${esc(meta)}</p>` : ''}${s.browser_open ? `<p class="watch-meta">Sign in to LinkedIn and any job sites in the application browser. Auto-apply waits until you select Done signing in.</p>` : ''}</div><div class="actions">${browser}${control}</div>`;
}
function renderAutoApply() { const panel = document.querySelector('#auto-apply'); if (panel) panel.innerHTML = autoApplyContent(); }
function renderApplicationList() {
  const list = document.querySelector('#application-list'), stats = document.querySelector('#application-stats');
  if (list && !list.contains(document.activeElement)) list.innerHTML = applicationList();
  if (stats) stats.innerHTML = applicationStats();
}
function startAutoApplyPolling() { clearTimeout(autoApply.timer); autoApply.timer = null; if (document.querySelector('#auto-apply')) pollAutoApply(); }
async function pollAutoApply() {
  if (!document.querySelector('#auto-apply')) return;
  try {
    autoApply.status = await api('/auto-apply');
    const s = autoApply.status, seen = [s.state,s.job?.id,s.applied,s.needs_you,s.waiting].join('|');
    // Show each application's progress without re-rendering the answers you may be typing.
    if (autoApply.seen !== null && seen !== autoApply.seen) { state = await api('/state'); renderApplicationList(); }
    autoApply.seen = seen;
    renderAutoApply();
  } catch { /* Keep the last known status; the next poll retries. */ }
  if (document.querySelector('#auto-apply')) autoApply.timer = setTimeout(pollAutoApply,autoApply.status?.enabled || autoApply.status?.browser_open ? 5000 : 30000);
}
function applicationStats() { return [['In progress',state.applications.filter(a => a.status !== 'applied').length,'clock','A little preparation goes a long way'],['Submitted',state.applications.filter(a => a.status === 'applied').length,'send','By you or auto-apply'],['Tailored resumes',state.jobs.filter(j => j.resume).length,'file','A considered introduction'],['Saved for later',state.jobs.filter(j => j.status === 'saved').length,'bookmark','Worth coming back to']].map(([label,value,glyph,note]) => `<div class="stat"><div class="stat-top">${label}${icon(glyph)}</div><div class="stat-number">${String(value).padStart(2,'0')}</div><div class="stat-bottom">${note}</div></div>`).join(''); }
function applicationStatus(a) {
  const id = `data-id="${esc(a.id)}"`;
  switch (a.status) {
    case 'applied': return badge('Submitted','','check');
    case 'applying': return badge('Applying now','blue','refresh');
    case 'needs_input': return badge('Needs your answer','amber','info');
    case 'needs_sign_in': return badge('Needs sign-in','amber','info') + button('Open application browser','apply-browser','globe','small',id);
    case 'failed': return badge('Didn’t finish','gray','info') + button('Try again','auto-apply-retry','refresh','small',id) + button('Mark submitted','confirm-application','check','small',id);
    default: return badge('In progress','amber','clock') + button('Mark submitted','confirm-application','check','small',id);
  }
}
function applicationList() {
  if (!state.applications.length) return empty('send','Your story is ready for its next audience.','Applications you open from a tailored job, or that auto-apply sends, appear here.',link('Explore opportunities','jobs','arrow','primary'));
  return [...state.applications].reverse().map(a => {
    const job = state.jobs.find(j => j.id === a.job_id);
    const question = a.status === 'needs_input' && a.question ? `<form class="answer-form inline-form" data-id="${esc(a.id)}"><label class="sr-only" for="answer-${esc(a.id)}">Your answer to: ${esc(a.question)}</label><input id="answer-${esc(a.id)}" name="answer" required maxlength="2000" placeholder="Your answer, used for every application" /><button class="btn small primary" type="submit">${icon('check')}Save answer</button></form>` : '';
    return `<article class="application-row"><div class="application-info"><div class="company-mark">${initial(job?.company)}</div><div><h3>${esc(job?.title || 'Job application')}</h3><p class="meta">${esc(job?.company)} · ${a.automated ? 'Auto-apply · ' : ''}Started ${dateLabel(a.created_at)}</p>${a.note ? `<p class="meta">${esc(a.note)}</p>` : ''}${question}</div></div><div class="application-right">${applicationStatus(a)}${button('View','view-job','arrow','text small',`data-id="${esc(a.job_id)}"`)}</div></article>`;
  }).join('');
}
function otherAnswer(item = {question:'',answer:''}) { return `<div class="other-answer"><label>Question<input name="other_question" value="${esc(item.question)}" maxlength="500" /></label><label>Answer<input name="other_answer" value="${esc(item.answer)}" maxlength="2000" /></label><button type="button" class="icon-btn" data-action="remove-other-answer" aria-label="Remove this question">${icon('x')}</button></div>`; }
function answersPanel() {
  const answers = state.application_answers || {};
  return `<section class="panel section-gap"><div class="panel-head"><div><h2>Your answers for applications</h2><p>Auto-apply uses only these, your profile, and each job’s tailored resume. Leave a field empty and it asks you when a form needs it. Self-identification questions are declined unless you answer them here.</p></div></div><form id="answers-form" class="panel-body"><div class="form-grid">${answerFields.map(([name,label,placeholder]) => field(label,name,answers[name] || '',{placeholder})).join('')}</div><h3 class="answers-subhead">Other questions</h3><div id="other-answers" class="stack">${(answers.other || []).map(otherAnswer).join('')}</div>${button('Add a question','add-other-answer','plus','text small')}<div class="form-footer"><p class="hint">Answers you give to waiting applications are added here too.</p><button class="btn primary" type="submit">${icon('check')}Save answers</button></div></form></section>`;
}
function rulesPanel() {
  const minimum = state.auto_apply?.min_ats || 0;
  return `<section class="panel section-gap"><div class="panel-head"><div><h2>Before each application</h2><p>Auto-apply runs the ATS check and adds the job’s keywords that your CV already has to that job’s resume. It can also skip jobs whose resume still covers too few of the job’s keywords.</p></div></div><form id="auto-apply-rules" class="panel-body inline-form"><label class="rules-field">Minimum ATS match (%)<input type="number" name="min_ats" min="0" max="100" step="5" value="${esc(minimum)}" /></label><p class="hint">0 applies regardless of the ATS match.</p><button class="btn" type="submit">${icon('check')}Save rule</button></form></section>`;
}
function applications() { return `${heading('Every step counts.','Keep track of the applications you’re moving forward.','','YOUR APPLICATIONS')}<section class="panel watch-panel" id="auto-apply" aria-live="polite">${autoApplyContent()}</section><div class="stats" id="application-stats">${applicationStats()}</div><section class="panel" id="application-list">${applicationList()}</section>${rulesPanel()}${answersPanel()}`; }
function assistant() {
  return `${heading('A little help with your next move.','Talk to Codex about your profile, job search, and applications.',button('New chat','codex-reset','plus'),'YOUR ASSISTANT')}
    <section class="panel codex-connection" id="codex-connection" aria-label="Codex connection"><p class="hint">Checking your ChatGPT connection…</p></section>
    <section class="panel chat-panel" aria-label="Conversation with Codex">
      <div class="panel-head"><h2>${icon('sparkles')} Your job search assistant</h2><span id="codex-activity" class="hint" role="status" aria-live="polite"></span></div>
      <div id="codex-messages" class="chat-messages" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions text" tabindex="0"></div>
      <div id="codex-pending" class="chat-pending"></div>
      <p id="codex-error" class="chat-error" role="alert" hidden></p>
      <form id="codex-chat-form" class="chat-composer"><label for="codex-prompt">How can I help?</label><textarea id="codex-prompt" name="message" maxlength="20000" rows="3" placeholder="Ask about your resume or tell me what to work on…" required>${esc(codex.draft)}</textarea><div class="chat-composer-footer"><span class="hint">Uses your ChatGPT account. Your Codex plan limits apply.</span><div class="actions">${button('Stop','codex-cancel','x','', 'disabled')}<button class="btn primary" type="submit" id="codex-send" disabled>${icon('send')}Send</button></div></div></form>
    </section>`;
}
function officialAuthURL(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password && !url.port && ['auth.openai.com','chatgpt.com'].includes(url.hostname) ? url.href : ''; } catch { return ''; }
}
function setHTMLIfChanged(element, html) {
  if (element && element.dataset.content !== html) { element.innerHTML = html; element.dataset.content = html; }
}
function questionField(question, index, pendingID) {
  const id = String(question.id || `question-${index}`), domID = `codex-answer-${encodeURIComponent(pendingID)}-${index}`;
  const text = question.question || question.header || question.title || id;
  const choices = Array.isArray(question.options) ? question.options : [];
  const options = choices.map(option => typeof option === 'string' ? {label:option} : option);
  return `<div class="chat-question"><label for="${esc(domID)}">${esc(text)}${question.description ? `<small>${esc(question.description)}</small>` : ''}</label>${options.length ? `<div class="chat-choices">${options.map(option => `<button type="button" class="btn small" data-action="codex-choice" data-target="${esc(domID)}" data-value="${esc(option.label || option.value || '')}">${esc(option.label || option.value || '')}</button>`).join('')}</div>${options.some(option => option.description) ? `<p class="hint">${options.map(option => `${esc(option.label || '')}: ${esc(option.description || '')}`).join(' · ')}</p>` : ''}` : ''}<input id="${esc(domID)}" name="${esc(id)}" type="${question.isSecret || question.is_secret ? 'password' : 'text'}" autocomplete="off" placeholder="${options.length ? 'Choose an option or write your answer' : 'Your answer'}" required maxlength="4000"/></div>`;
}
function renderCodexPanels() {
  const connection = document.querySelector('#codex-connection');
  if (!connection) return;
  const status = codex.status, authenticated = Boolean(status?.authenticated), available = Boolean(status?.available);
  const label = !status ? 'Checking connection' : authenticated ? 'Connected with ChatGPT' : available ? 'Sign in to ChatGPT' : 'Codex needs setup';
  const note = codex.error || codex.pollError || status?.message || (authenticated ? 'Codex can help with your workspace using your ChatGPT subscription.' : 'Sign in to use Codex in this app. No GPT API key is needed.');
  const identity = [status?.email,status?.plan].filter(Boolean).join(' · ');
  const authURL = officialAuthURL(codex.authURL);
  setHTMLIfChanged(connection,`<div class="codex-connection-copy"><strong>${icon(authenticated ? 'check' : 'sparkles')}${esc(label)}</strong><p>${esc(note)}</p>${identity ? `<p>${esc(identity)}</p>` : ''}${status?.login_pending ? '<p>Finish signing in in your browser. This page will check automatically.</p>' : ''}${authURL && !authenticated ? `<a class="btn text small" href="${esc(authURL)}" target="_blank" rel="noopener noreferrer">Continue ChatGPT sign-in ${icon('up')}</a>` : ''}</div><div class="actions">${!authenticated ? button(status?.login_pending ? 'Open sign-in again' : 'Sign in with ChatGPT','codex-login','up','primary',!available || codex.action ? 'disabled' : '') : ''}${button('Refresh','codex-refresh','refresh','small',codex.action ? 'disabled' : '')}</div>`);
  const log = document.querySelector('#codex-messages');
  if (!log) return;
  const messages = Array.isArray(codex.chat.messages) ? codex.chat.messages : [];
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 90;
  const messageHTML = messages.length ? messages.map(message => {
    const role = ['user','assistant','tool'].includes(message.role) ? message.role : 'assistant';
    const speaker = role === 'user' ? 'You' : role === 'tool' ? 'Activity' : 'Codex';
    return `<article class="chat-message ${role}"><div class="chat-message-label">${icon(role === 'user' ? 'file' : role === 'tool' ? 'code' : 'sparkles')}<strong>${speaker}</strong></div><div class="chat-message-text">${esc(message.content)}</div></article>`;
  }).join('') : `<div class="chat-welcome"><div class="empty-icon">${icon('sparkles')}</div><h3>What would you like to work on?</h3><p>Ask me to explore jobs, organize your experience, or help prepare an application.</p><div class="chat-suggestions">${['Find jobs in the US and Europe','Show my saved jobs','Help me improve my resume'].map(prompt => button(esc(prompt),'codex-suggestion','arrow','',`data-prompt="${esc(prompt)}"`)).join('')}</div></div>`;
  const changed = log.dataset.content !== messageHTML;
  setHTMLIfChanged(log,messageHTML);
  if (changed && nearBottom) log.scrollTop = log.scrollHeight;
  const pending = Array.isArray(codex.chat.pending) ? codex.chat.pending : [];
  setHTMLIfChanged(document.querySelector('#codex-pending'),pending.map(item => `<section class="chat-request"><h3>${esc(item.title || (item.kind === 'approval' ? 'Your approval is needed' : 'A question for you'))}</h3>${item.description ? `<p>${esc(item.description)}</p>` : ''}${item.kind === 'approval' ? `<div class="actions">${button('Decline','codex-approval','x','',`data-id="${esc(item.id)}" data-approved="false"`)}${button('Approve','codex-approval','check','primary',`data-id="${esc(item.id)}" data-approved="true"`)}</div>` : item.kind === 'question' ? `<form class="codex-question-form" data-id="${esc(item.id)}">${(Array.isArray(item.questions) ? item.questions : []).map((question,index) => questionField(question,index,item.id)).join('')}<div class="form-footer"><button class="btn primary" type="submit">Send answers ${icon('arrow')}</button></div></form>` : '<p class="hint">This request cannot be answered here. Stop this turn and try again.</p>'}</section>`).join(''));
  document.querySelectorAll('#codex-pending button').forEach(button => { button.disabled = codex.action; });
  const error = codex.error || codex.pollError || codex.chat.error || '';
  const errorBox = document.querySelector('#codex-error'); errorBox.textContent = error; errorBox.hidden = !error;
  const busy = Boolean(codex.chat.busy);
  document.querySelector('#codex-activity').textContent = pending.length ? 'Waiting for you' : busy ? 'Working…' : '';
  document.querySelector('#codex-send').disabled = codex.action || busy || !authenticated || !available || !codex.draft.trim();
  document.querySelector('[data-action="codex-cancel"]').disabled = codex.action || !busy;
  document.querySelector('[data-action="codex-reset"]').disabled = codex.action || busy;
  document.querySelectorAll('[data-action="codex-suggestion"]').forEach(button => { button.disabled = codex.action || busy || !authenticated || !available; });
}
function stopCodexPolling() {
  clearTimeout(codex.timer); codex.timer = null;
  codex.controller?.abort(); codex.controller = null; codex.generation++;
}
function startCodexPolling() {
  stopCodexPolling();
  if (!document.querySelector('#codex-connection')) return;
  renderCodexPanels();
  const generation = codex.generation;
  codex.timer = setTimeout(() => pollCodex(generation),0);
}
async function pollCodex(generation) {
  if (generation !== codex.generation || !document.querySelector('#codex-connection')) return;
  const controller = new AbortController(); codex.controller = controller;
  const paths = currentRoute === 'assistant' ? ['/codex/status','/codex/chat'] : ['/codex/status'];
  const results = await Promise.allSettled(paths.map(path => api(path,{signal:controller.signal})));
  if (generation !== codex.generation || controller.signal.aborted) return;
  let error = '';
  results.forEach((result,index) => {
    if (result.status === 'fulfilled') { if (index === 0) codex.status = result.value; else codex.chat = result.value; }
    else error = result.reason?.message || 'Could not reach Codex. Try refreshing the connection.';
  });
  codex.pollError = error;
  renderCodexPanels();
  codex.timer = setTimeout(() => pollCodex(generation),error ? 10000 : codex.chat.busy || codex.status?.login_pending ? 1200 : 8000);
}
async function codexAction(work) {
  if (codex.action) return;
  codex.action = true; codex.error = ''; stopCodexPolling(); renderCodexPanels();
  try { await work(); } catch (error) { codex.error = error.message; }
  finally { codex.action = false; startCodexPolling(); }
}
async function sendCodexMessage(message) {
  if (!message.trim() || codex.action || codex.chat.busy || !codex.status?.authenticated) return;
  codex.draft = message;
  await codexAction(async () => {
    codex.chat = await api('/codex/chat/message',{method:'POST',body:{message:message.trim()}});
    codex.draft = '';
    const input = document.querySelector('#codex-prompt'); if (input) { input.value = ''; input.focus(); }
  });
}
function providerFields(provider, values) {
  const isCodex = provider === 'codex';
  return `${isCodex ? '<section class="full codex-connection" id="codex-connection" aria-label="Codex connection"><p class="hint">Checking your ChatGPT connection…</p></section>' : field('API base URL','base_url',values.base_url,{type:'url',required:true,full:true})}${field(isCodex ? 'Model name (optional)' : 'Model name','model',values.model,{required:!isCodex,full:true,placeholder:isCodex ? 'Automatic — use your account’s default model' : 'Use the name of an installed model'})}${isCodex ? '<p class="hint full">ChatGPT sign-in is managed by Codex on this computer. No API key is required.</p>' : field('API key (optional for local AI)','api_key','',{type:'password',full:true,placeholder:state.settings.api_key_set ? 'A key is saved. Leave blank to keep it.' : 'Only needed if your provider requires one'}) + (state.settings.api_key_set ? '<label class="check-label full"><input name="clear_api_key" type="checkbox"/> Remove the saved API key</label>' : '')}`;
}
function settings() {
  const s = state.settings;
  providerDrafts[s.provider] = {base_url:s.base_url || '',model:s.model || ''};
  return `${heading('A workspace that works for you.','Connect your AI and keep your search close to home.','','YOUR SETTINGS')}<div class="settings-layout"><section class="panel"><div class="panel-head"><h2>Your AI connection</h2>${badge(s.provider === 'codex' ? 'ChatGPT sign-in' : s.provider === 'ollama' ? 'Local AI' : 'Custom provider','gray','sparkles')}</div><form id="settings-form" class="form-pad" style="padding-top:8px" data-provider="${esc(s.provider)}"><div class="form-grid"><label class="full">Provider<select name="provider" id="ai-provider"><option value="codex" ${s.provider === 'codex' ? 'selected' : ''}>Codex — sign in with ChatGPT</option><option value="ollama" ${s.provider === 'ollama' ? 'selected' : ''}>Ollama — local on your computer</option><option value="compatible" ${s.provider === 'compatible' ? 'selected' : ''}>Compatible chat API — custom endpoint</option></select></label></div><div class="form-grid section-gap" id="provider-fields">${providerFields(s.provider,s)}</div><div class="notice section-gap">${icon('shield')}<span>Codex uses your ChatGPT account and sends prompts to OpenAI. Ollama processes your resume locally. A custom endpoint receives the content you send to it. Your workspace stays on this computer.</span></div><div class="form-footer">${button('Test connection','test-ai','refresh')}<button class="btn primary" type="submit">Save settings ${icon('check')}</button></div></form></section><div><section class="panel"><div class="panel-head"><h2>ChatGPT, connected to your workspace</h2></div><div class="panel-body"><p class="hint">Choose Codex to use your ChatGPT subscription for resume assistance and job-search conversations. Sign in once, then open the Assistant. No GPT API key is needed.</p><div class="section-gap">${link('Open Assistant','assistant','sparkles','primary')}</div></div></section><section class="panel section-gap"><div class="panel-head"><h2>Prefer AI on your computer?</h2></div><div class="panel-body"><p class="hint" style="margin-bottom:15px">Install Ollama, download a model, then enter its exact name in your AI connection.</p><pre class="code">ollama pull llama3.2
ollama serve</pre>${external('Ollama setup','https://ollama.com/download','up','text small')}</div></section><section class="panel section-gap"><div class="panel-head"><h2>Your application browser</h2></div><div class="panel-body"><p class="hint">Camoufox opens a separate browser to help fill application forms. You complete login, review the answers, and submit.</p><pre class="code">pip install -r requirements-browser.txt
python -m camoufox fetch</pre><p class="hint section-gap">Run these in the project’s activated virtual environment.</p>${button('Check browser','check-browser','globe','text small')}</div></section></div></div>`;
}
const views = {overview,assistant,profile,preferences,sources,jobs,applications,settings};

function openModal(title, body) { modal.innerHTML = `<div class="modal-head"><h2 id="modal-title">${title}</h2><button class="icon-btn" data-action="close-modal" aria-label="Close dialog">${icon('x')}</button></div><div class="modal-body">${body}</div>`; if (!modal.open) modal.showModal(); }
function showImport(mode = 'resume') {
  const skillsOnly = mode === 'skills';
  openModal(skillsOnly ? 'Bring your CV skills into your profile' : 'Your experience, imported.',`<p class="modal-description">${skillsOnly ? 'Upload your CV to recover its technical skills and methods. Extracted skills are confirmed automatically; you can change them anytime.' : 'Choose your CV as a PDF. AI reads each page, and your own wording is kept. All extracted information is accepted by default and can be edited anytime.'}</p><form id="import-form" data-mode="${skillsOnly ? 'skills' : 'resume'}"><label>Your CV<input type="file" name="file" accept=".pdf,application/pdf" required aria-describedby="import-error"/></label><div class="notice section-gap">${icon('info')}<span>${skillsOnly ? 'This import adds confirmed skills and keeps your existing experience entries. Choose your CV as a PDF.' : 'Imported experience and skills are ready for your resumes and job matching immediately. Importing the same CV again updates its entries instead of duplicating them; your other entries are kept. With Codex, your master resume is then built with its deepest reasoning in the background. Word, Pages, or Google Docs CVs need to be saved as PDF first. Reading a CV can take a few minutes.'}</span></div><p id="import-status" class="import-status section-gap" role="status" aria-live="polite" hidden></p><p id="import-error" class="import-error section-gap" role="alert" tabindex="-1" hidden></p><div class="form-footer"><button class="btn primary" type="submit">${icon('sparkles')}${skillsOnly ? 'Import skills' : 'Import & extract'}</button></div></form>`);
}

async function importResume(form, submitButton) {
  if (form.dataset.importing === 'true') return;
  const skillsOnly = form.dataset.mode === 'skills';
  const fileInput = form.elements.file, file = fileInput.files[0];
  if (!file) return;
  const errorBox = form.querySelector('#import-error'), statusBox = form.querySelector('#import-status');
  const closeButton = modal.querySelector('[data-action="close-modal"]');
  const original = submitButton.innerHTML, controller = new AbortController(), started = Date.now();
  let timer, timeout, saved = false;
  errorBox.hidden = true; errorBox.textContent = '';
  statusBox.hidden = false; statusBox.textContent = 'Preparing your CV…';
  form.dataset.importing = 'true'; form.setAttribute('aria-busy','true'); busyCount++;
  submitButton.disabled = true; fileInput.disabled = true; closeButton.disabled = true;
  submitButton.innerHTML = `${icon('refresh','spinner')} Importing…`;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error('Choose a file smaller than 10 MB.');
    if (!file.size) throw new Error('This file is empty. Choose a PDF of your CV.');
    const header = new TextDecoder().decode(await file.slice(0,1024).arrayBuffer());
    const name = file.name.trim().toLowerCase(), type = file.type.toLowerCase();
    if (!header.includes('%PDF-') && !/\.pdf$/.test(name) && type !== 'application/pdf') {
      throw new Error('Choose your CV as a PDF. In Word, Pages, or Google Docs, save or download it as a PDF first.');
    }
    const updateStatus = () => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      statusBox.textContent = `${skillsOnly ? 'Extracting technical skills from your CV' : 'Reading your CV and preparing your profile'}… ${seconds}s elapsed. Larger resumes can take a few minutes. Keep this window open.`;
    };
    updateStatus(); timer = setInterval(updateStatus,1000);
    // The server bounds AI extraction to five minutes. Keep a final network
    // deadline too, so a lost connection cannot leave this dialog busy forever.
    timeout = setTimeout(() => controller.abort(),630000);
    const data = new FormData(); data.append('file',file);
    const result = await api(skillsOnly ? '/import/skills' : '/import',{method:'POST',body:data,signal:controller.signal});
    saved = true; statusBox.textContent = skillsOnly ? 'Imported and confirmed. Opening your skills…' : 'Imported and confirmed. Opening your profile…';
    // Fetch the saved data before closing: a failed refresh must not suggest
    // retrying the upload and accidentally append duplicate entries.
    state = await api('/state',{signal:controller.signal});
    modal.close(); location.hash = skillsOnly ? 'profile?skills' : 'profile'; render();
    if (skillsOnly) scrollToSkills();
    toast(result.message || (skillsOnly ? 'CV skills imported and confirmed. You can change them anytime.' : 'CV imported and confirmed. You can edit your profile anytime.'));
  } catch (error) {
    statusBox.hidden = true;
    errorBox.textContent = saved
      ? `${skillsOnly ? 'Your skills were' : 'Your resume was'} imported, but the profile could not refresh. Close this window and reload the page to see your entries. Do not upload the same file again.`
      : error.name === 'AbortError'
        ? 'The upload connection timed out. Check My profile before retrying in case the import finished.'
        : error.message || 'The CV could not be imported. Please try again.';
    errorBox.hidden = false; errorBox.focus();
  } finally {
    clearInterval(timer); clearTimeout(timeout); busyCount--;
    form.dataset.importing = 'false'; form.removeAttribute('aria-busy');
    submitButton.disabled = saved; submitButton.innerHTML = original;
    fileInput.disabled = false; closeButton.disabled = false;
  }
}
function showItem(item) { openModal(item ? 'Edit your experience' : 'Add to your story',`<p class="modal-description">Describe what you actually did. AI will suggest broader language without adding new achievements or qualifications.</p><form id="item-form" data-id="${esc(item?.id || '')}"><div class="form-grid"><label class="full">Type<select name="kind">${[['experience','Work experience'],['project','Project'],['education','Education'],['publication','Publication']].map(([value,label]) => `<option value="${value}" ${item?.kind === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${field('Title','title',item?.title,{required:true,placeholder:'Role, project, degree, or publication'})}${field('Organization','organization',item?.organization)}${field('Start date','start',item?.start,{placeholder:'e.g. Jan 2022'})}${field('End date','end',item?.end,{placeholder:'e.g. Present'})}${textarea('What did you do?','original',item?.original)}</div><div class="form-footer"><span class="hint">Saved as a draft for your review.</span><button class="btn primary" type="submit">${icon('sparkles')}Save & enhance</button></div></form>`); }
function showReview(item) { openModal('Your experience. Your words.',`<p class="modal-description">Check that the suggestion accurately represents your work. You can edit the wording before confirming it.</p><form id="review-form" data-id="${esc(item.id)}"><div class="compare"><div><h3>Original description</h3><p>${esc(item.original)}</p></div><div><h3>Suggested description</h3><label><span class="sr-only">Reviewed description</span><textarea name="enhanced" required>${esc(item.enhanced || item.original)}</textarea></label></div></div><label class="check-label confirm-line"><input type="checkbox" required/> This accurately describes my experience.</label><div class="form-footer">${button('Rephrase with AI','enhance-item','sparkles','',`data-id="${esc(item.id)}"`)}<button class="btn primary" type="submit">${icon('check')}Confirm experience</button></div></form>`); }
function showSource(source) { openModal(source ? 'Edit job source' : 'Find another source',`<p class="modal-description">Connect a company’s public career board, LinkedIn browser search, or a page that publishes structured JobPosting data.</p><form id="source-form" data-id="${esc(source?.id || '')}"><div class="form-grid">${field('Source name','name',source?.name,{full:true,required:true,placeholder:'e.g. My favourite company'})}<label class="full">Source type<select name="kind">${[['greenhouse','Greenhouse company board'],['lever','Lever company board'],['linkedin','LinkedIn jobs (Camoufox)'],['camoufox','Custom careers page (Camoufox)'],['remotive','Remotive remote jobs']].map(([value,label]) => `<option value="${value}" ${source?.kind === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${field('Career board URL','url',source?.kind === 'linkedin' ? LINKEDIN_SEARCH_URL : source?.url,{type:'url',required:true,full:true,placeholder:'https://boards.greenhouse.io/company'})}<label class="check-label full"><input type="checkbox" name="enabled" ${!source || source.enabled ? 'checked' : ''}/> Include this source in my search</label></div><div class="form-footer"><button class="btn primary" type="submit">${icon('check')}Save source</button></div></form>`); }
function showLinkedIn() {
  openModal('Search LinkedIn',`<p class="modal-description">Explore a batch of jobs in the United States and Europe, or add keywords to narrow your search. Jobs that match neither your keywords nor your target positions are skipped; with neither, every title is kept.</p><form id="linkedin-form"><div class="form-grid">${field('Keywords or job title (optional)','keywords','',{full:true,placeholder:'Any job title'})}<div class="full"><p class="hint" id="linkedin-regions-label">Search regions</p><div class="actions" role="group" aria-labelledby="linkedin-regions-label"><label class="check-label"><input type="checkbox" name="region" value="United States" checked/> United States</label><label class="check-label"><input type="checkbox" name="region" value="Europe" checked/> Europe</label></div></div>${field('Custom location (optional)','location','',{full:true,placeholder:'Overrides the selected regions, e.g. London'})}<label>Maximum jobs per region<input type="number" name="limit" min="1" max="25" step="1" value="10" required/></label><label class="check-label"><input type="checkbox" name="remote_only"/> Only remote opportunities</label></div><div class="notice section-gap">${icon('globe')}<span>Your LinkedIn sign-in is kept in a local browser profile for future searches. Complete any sign-in or verification in the opened browser. Results are a limited batch and some descriptions may be partial.</span></div><div id="linkedin-status" role="status" aria-live="polite"></div><div class="form-footer"><button class="btn primary" type="submit">${icon('search')}Search LinkedIn</button></div></form>`);
}
function showAddJob() { openModal('Found something interesting?',`<p class="modal-description">Save a job from anywhere. Paste the full description so we can focus your resume on the right experience.</p><form id="job-form"><div class="form-grid">${field('Job title','title','',{required:true})}${field('Company','company','',{required:true})}${field('Location','location','',{placeholder:'e.g. Remote, New York'})}${field('Salary (optional)','salary')}${field('Job URL','url','',{type:'url',required:true,full:true})}${textarea('Full job description','description','',true)}</div><div class="form-footer"><button class="btn primary" type="submit">${icon('bookmark')}Save opportunity</button></div></form>`); }
function matchDetails(job) {
  const match = job.ai_match;
  if (!match) return job.match_error ? `<div class="notice">${icon('info')}<span>Matching this job to your master CV didn’t work this time (${esc(job.match_error.message)}). It will be tried again.</span></div>` : '';
  const list = (items, glyph, label) => items.length ? `<p class="match-label">${label}</p><ul class="match-list">${items.map(text => `<li>${icon(glyph)}<span>${esc(text)}</span></li>`).join('')}</ul>` : '';
  return `<section class="match-details"><h3>How you match</h3><p>${esc(match.summary)}</p>${list(match.strengths,'check','Strengths')}${list(match.gaps,'x','Gaps')}<p class="hint">Compared with your master CV${match.model ? ` by ${esc(match.model)}${match.effort ? ` at ${esc(match.effort)} reasoning` : ''}` : ''}.</p></section>`;
}
function showJob(job) {
  if (!job) return toast('That job is no longer available in your workspace.', true);
  openModal(esc(job.title),`<p class="modal-description">${esc(job.company)} · ${esc(job.location || 'Location not specified')}</p><div class="actions">${job.ats_score != null ? badge(`ATS ${job.ats_score}%${job.ats_before != null && job.ats_before !== job.ats_score ? ` (was ${job.ats_before}%)` : ''}`,'','search') : badge('ATS not checked yet','gray','search')}${badge(job.status,'gray')}${external('Original listing',job.url,'up','text small')}</div>${matchDetails(job)}<div class="detail-grid"><div><small>Source</small><p>${esc(job.source)}</p></div><div><small>Compensation</small><p>${esc(job.salary || 'Not specified')}</p></div></div>${job.resume ? `<div class="notice">${icon('check')}<span>Your tailored resume is ready. Review it before opening the application.</span></div><div class="actions" style="margin-bottom:20px"><a class="btn small" href="/api/jobs/${encodeURIComponent(job.id)}/resume" target="_blank" rel="noopener">${icon('eye')}Preview resume</a><a class="btn small" href="/api/jobs/${encodeURIComponent(job.id)}/resume.pdf" target="_blank" rel="noopener">${icon('download')}Download PDF</a></div>` : ''}<section class="ats-check" id="ats-check" data-id="${esc(job.id)}" aria-live="polite"><p class="hint">${icon('refresh','spinner')} Reading your resume the way an ATS does…</p></section><div class="description">${esc(job.description)}</div><div class="job-skills-prompt"><p>Review techniques and tools relevant to this opportunity.</p>${button('Suggest relevant skills','suggest-job-skills','sparkles','small',`data-id="${esc(job.id)}"`)}<p id="job-skills-status" class="import-error" role="alert" hidden></p></div><div class="form-footer">${button(job.status === 'archived' ? 'Restore' : 'Archive',job.status === 'archived' ? 'save-job' : 'archive-job','bookmark','text small',`data-id="${esc(job.id)}"`)}${button(job.resume ? 'Re-tailor resume' : 'Tailor my resume','tailor-job','sparkles',job.resume ? '' : 'primary',`data-id="${esc(job.id)}"`)}${job.resume ? button('Open application','apply-job','up','primary',`data-id="${esc(job.id)}"`) : ''}</div>`);
  loadAts(job.id);
}
function atsContent(id, result) {
  const intro = `<h3>ATS check</h3><p>See how ${state.jobs.find(job => job.id === id)?.resume ? 'this resume' : 'your general resume'} reads to an applicant tracking system, and which of the job’s keywords it contains.</p>`;
  if (!result?.ready) return `${intro}${result?.message ? `<p class="hint">${esc(result.message)}</p>` : button('Check ATS','ats-check','search','small primary',`data-id="${esc(id)}"`)}`;
  const groups = {present:[],variant:[],addable:[],missing:[]};
  for (const term of result.terms) groups[term.status].push(term);
  const chip = term => `<span class="chip ats-term ${term.status}" title="${esc([term.importance === 'required' ? 'Required' : 'Preferred',term.variants?.length ? `also: ${term.variants.join(', ')}` : '',term.where ? `in ${term.where}: “${term.wording}”` : term.wording ? `your resume says “${term.wording}”` : ''].filter(Boolean).join(' · '))}">${esc(term.term)}${term.importance === 'required' ? '<b aria-label="required">*</b>' : ''}</span>`;
  const row = (title, items, note = '') => items.length ? `<div class="ats-group"><p class="match-label">${title} (${items.length})</p>${note}<div class="chip-list">${items.map(chip).join('')}</div></div>` : '';
  const level = {ok:['check','ok'],warn:['info','warn'],fail:['x','fail']};
  const checks = result.checks.map(c => `<li class="${level[c.level][1]}">${icon(level[c.level][0])}<span><strong>${esc(c.label)}:</strong> ${esc(c.detail)}</span></li>`).join('');
  return `<div class="ats-head"><h3>ATS check</h3><div class="ats-score"><strong>${result.score}%</strong><span>keyword match${result.before !== result.score ? ` · <em class="match-before">was ${result.before}%</em>` : ''}</span></div></div><div class="progress-track ats-bar"><span style="width:${Number(result.score) || 0}%"></span></div>
    <p class="match-label">Parsing</p><ul class="match-list ats-checks">${checks}</ul>
    ${result.wording?.length ? `<p class="match-label">Using the posting’s names</p><ul class="match-list ats-checks">${result.wording.map(change => `<li class="ok">${icon('check')}<span>${esc(change.from)} → ${esc(change.to)}</span></li>`).join('')}</ul>` : ''}
    ${row('In your resume',groups.present)}
    ${row('Under another name',groups.variant,'<p class="hint">Your resume says these differently. ATS searches use the posting’s words, so they count half. If one means the same thing, use the posting’s name in your CV.</p>')}
    ${row('In your CV, not in this resume',groups.addable,result.tailored ? `<p class="hint">Add them to this resume’s skills, worded as in your CV. ${button(`Add ${groups.addable.length}`,'ats-add','plus','small',`data-id="${esc(id)}"`)}</p>` : '<p class="hint">Tailor a resume for this job to add them.</p>')}
    ${row('Not in your CV',groups.missing,'<p class="hint">Not added unless you confirm them. If you have any, tick them in Goldmove on Discover jobs.</p>')}
    <p class="hint">Required keywords are marked * and count double; one under another name counts half. An estimate: employers’ own ATS rankings aren’t visible. Keywords listed from the posting${result.model ? ` by ${esc(result.model)}` : ''}; matching is literal, as ATS searches are. ${button('List keywords again','ats-refresh','refresh','text small',`data-id="${esc(id)}"`)}</p>`;
}
function renderAts(id, result) { const panel = document.querySelector(`#ats-check[data-id="${CSS.escape(id)}"]`); if (panel) panel.innerHTML = atsContent(id,result); }
async function loadAts(id) {
  try { renderAts(id,await api(`/jobs/${encodeURIComponent(id)}/ats`)); }
  catch (error) { renderAts(id,{ready:false,message:error.message}); }
}
function confirmDialog(title, text, action, id, label = 'Confirm', cls = 'primary') { openModal(title,`<p class="modal-description">${esc(text)}</p><div class="form-footer">${button('Cancel','close-modal','','')}${button(label,action,'check',cls,`data-id="${esc(id)}"`)}</div>`); }

document.addEventListener('click', async (event) => {
  if (event.target.closest('a[href^="#"]')) document.querySelector('.shell')?.classList.remove('menu-open');
  const b = event.target.closest('[data-action]'); if (!b) return;
  const {action,id} = b.dataset;
  if (b.disabled) return;
  const item = state?.items.find(i => i.id === id), job = state?.jobs.find(j => j.id === id);
  switch(action) {
    case 'codex-refresh': codex.error = ''; startCodexPolling(); break;
    case 'codex-login': await codexAction(async () => {
      if (codex.status?.login_pending && officialAuthURL(codex.authURL)) { window.open(codex.authURL,'_blank','noopener,noreferrer'); return; }
      const result = await api('/codex/login',{method:'POST',body:{}});
      if (!result.auth_url) { codex.status = await api('/codex/status'); return; }
      const authURL = officialAuthURL(result.auth_url);
      if (!authURL) throw new Error('Codex did not return a trusted ChatGPT sign-in link. Refresh the connection and try again.');
      codex.authURL = authURL; codex.status = {...codex.status,login_pending:true,message:result.message};
      window.open(authURL,'_blank','noopener,noreferrer');
    }); break;
    case 'codex-suggestion': await sendCodexMessage(b.dataset.prompt); break;
    case 'codex-cancel': await codexAction(async () => { codex.chat = await api('/codex/chat/cancel',{method:'POST',body:{}}); }); break;
    case 'codex-reset': await codexAction(async () => { codex.chat = await api('/codex/chat/reset',{method:'POST',body:{}}); }); break;
    case 'codex-approval': await codexAction(async () => { codex.chat = await api('/codex/chat/respond',{method:'POST',body:{id,approved:b.dataset.approved === 'true'}}); }); break;
    case 'codex-choice': { const input = document.getElementById(b.dataset.target); if (input) { input.value = b.dataset.value; input.focus(); } break; }
    case 'menu': document.querySelector('.shell').classList.toggle('menu-open'); break;
    case 'close-modal': modal.close(); break;
    case 'import': showImport(); break;
    case 'import-skills': showImport('skills'); break;
    case 'add-item': showItem(); break;
    case 'edit-item': showItem(item); break;
    case 'edit-xyz': showXyz(item); break;
    case 'master-xyz': { const result = await task(b,() => api('/master-resume/xyz',{method:'POST'})); if (result) { master.status = result; renderMaster(); startMasterPolling(); } break; }
    case 'review-item': if (item) showReview(item); break;
    case 'enhance-item': { const result = await task(b,() => api(`/items/${id}/enhance`,{method:'POST'})); if (result) showReview(state.items.find(i => i.id === id)); break; }
    case 'delete-item': confirmDialog('Remove this experience?','This removes it from your profile. Existing tailored resumes may need to be regenerated.','delete-item-confirmed',id,'Remove item','danger'); break;
    case 'delete-item-confirmed': await task(b,() => api(`/items/${id}`,{method:'DELETE'}),{close:true,message:'Experience removed.'}); break;
    case 'suggest-skills': { const result = await task(b,() => api('/suggestions/skills',{method:'POST'}),{message:'Skill suggestions are ready for your review.'}); if (result) scrollToSkills(); break; }
    case 'suggest-job-skills': {
      const errorBox = document.querySelector('#job-skills-status');
      if (errorBox) { errorBox.hidden = true; errorBox.textContent = ''; }
      const result = await task(b,async () => {
        try { return await api('/suggestions/skills',{method:'POST',body:{job_id:id}}); }
        catch (error) { if (errorBox) { errorBox.hidden = false; errorBox.textContent = error.message; } throw error; }
      },{close:true,message:'Job-relevant skill suggestions are ready for review.'});
      if (result) reviewSkills();
      break;
    }
    case 'suggest-positions': { const result = await task(b,() => api('/master-resume/roles',{method:'POST'})); if (result) { master.status = result; renderMaster(); startMasterPolling(); } break; }
    case 'remove-chip': await task(b,() => api(`/${b.dataset.bag}`,{method:'PUT',body:{items:state[b.dataset.bag].filter(i => i.id !== id)}})); break;
    case 'add-source': showSource(); break;
    case 'edit-source': showSource(state.sources.find(s => s.id === id)); break;
    case 'delete-source': confirmDialog('Remove this source?','Saved jobs from this source will remain in your workspace.','delete-source-confirmed',id,'Remove source','danger'); break;
    case 'delete-source-confirmed': await task(b,() => api('/sources',{method:'PUT',body:{items:state.sources.filter(s => s.id !== id)}}),{close:true,message:'Source removed.'}); break;
    case 'discover': await task(b,() => api('/jobs/discover',{method:'POST'})); break;
    case 'search-linkedin': showLinkedIn(); break;
    case 'auto-apply-start': case 'auto-apply-stop': { const result = await task(b,() => api(`/auto-apply/${action === 'auto-apply-start' ? 'start' : 'stop'}`,{method:'POST'}),{message:action === 'auto-apply-start' ? 'Auto-apply is on. It applies to jobs at 70% or more, one at a time.' : 'Auto-apply is off.'}); if (result) { autoApply.status = result; renderAutoApply(); startAutoApplyPolling(); } break; }
    case 'apply-browser': case 'apply-browser-close': { const result = await task(b,() => api(action === 'apply-browser' ? '/auto-apply/browser' : '/auto-apply/browser/close',{method:'POST',body:action === 'apply-browser' ? {application_id:id || ''} : undefined})); if (result) { autoApply.status = result; renderAutoApply(); startAutoApplyPolling(); } break; }
    case 'ats-check': case 'ats-refresh': case 'ats-add': {
      const path = `/jobs/${encodeURIComponent(id)}/ats${action === 'ats-add' ? '/add' : ''}`;
      const result = await task(b,() => api(path,{method:'POST',body:action === 'ats-refresh' ? {refresh:true} : undefined}));
      if (result) renderAts(id,result);
      break;
    }
    case 'rebuild-term-bags': await task(b,() => api('/term-bags/rebuild',{method:'POST'}),{message:'Listing the other names for your terms again. This takes about a minute.'}); break;
    case 'gold-toggle': gold.showAll = !gold.showAll; renderGoldmove(true); break;
    case 'gold-select-all': { const boxes = [...document.querySelectorAll('#goldmove-form input[name="term"]')]; const all = boxes.every(box => box.checked); boxes.forEach(box => { box.checked = !all; }); break; }
    case 'gold-dismiss': case 'gold-undismiss': {
      const terms = goldTerms();
      if (action === 'gold-dismiss' && !terms.length) return toast('Tick the keywords that aren’t yours first.',true);
      const result = await task(b,() => api(action === 'gold-dismiss' ? '/goldmove/dismiss' : '/goldmove/dismissed/clear',{method:'POST',body:action === 'gold-dismiss' ? {terms} : undefined}));
      if (result) { gold.status = result; renderGoldmove(true); }
      break;
    }
    case 'regroup-skills': { const result = await task(b,() => api('/skill-groups/regroup',{method:'POST'})); if (result) { grouper.status = result; startSkillGroupPolling(); } break; }
    case 'auto-apply-retry': await task(b,() => api(`/auto-apply/applications/${id}/retry`,{method:'POST'})); break;
    case 'add-other-answer': { const list = document.querySelector('#other-answers'); list.insertAdjacentHTML('beforeend',otherAnswer()); list.lastElementChild.querySelector('input').focus(); break; }
    case 'remove-other-answer': b.closest('.other-answer').remove(); break;
    case 'match-jobs': { const result = await task(b,() => api('/job-matching/start',{method:'POST'})); if (result) { matching.status = result; renderMatching(); startMatchingPolling(); } break; }
    case 'master-rebuild': { const result = await task(b,() => api('/master-resume/rebuild',{method:'POST'})); if (result) { master.status = result; renderMaster(); startMasterPolling(); } break; }
    case 'watch-start': case 'watch-stop': { const result = await task(b,() => api(`/linkedin/continuous/${action === 'watch-start' ? 'start' : 'stop'}`,{method:'POST'})); if (result) { watch.status = result; renderWatch(); } break; }
    case 'add-job': showAddJob(); break;
    case 'view-job': showJob(job); break;
    case 'filter-jobs': jobFilter = b.dataset.filter; render(); break;
    case 'save-job': case 'archive-job': {
      const result = await task(b,() => api(`/jobs/${id}`,{method:'PATCH',body:{status:action === 'save-job' ? 'saved' : 'archived'}}),{message:action === 'save-job' ? 'Job saved.' : 'Job archived.'});
      if (result && modal.open) showJob(state.jobs.find(j => j.id === id)); break;
    }
    case 'tailor-job': { const result = await task(b,() => api(`/jobs/${id}/tailor`,{method:'POST'})); if (result) showJob(state.jobs.find(j => j.id === id)); break; }
    case 'apply-job': await task(b,() => api(`/jobs/${id}/apply`,{method:'POST'}),{close:true}); break;
    case 'confirm-application': confirmDialog('Did you submit this application?','Confirm only after you have submitted the form on the employer’s website. This updates your tracker.','confirm-application-final',id,'Yes, I submitted it'); break;
    case 'confirm-application-final': await task(b,() => api(`/applications/${id}/confirm`,{method:'POST'}),{close:true,message:'A step forward. Application marked as submitted.'}); break;
    case 'resume': window.open('/api/resume','_blank','noopener'); break;
    case 'test-ai': {
      const form = document.querySelector('#settings-form');
      if (!form.reportValidity()) break;
      await task(b,async () => { const values=formValues(form); values.clear_api_key = Boolean(form.elements.clear_api_key?.checked); await api('/settings',{method:'PUT',body:values}); return api('/settings/test',{method:'POST'}); }); break;
    }
    case 'check-browser': await task(b,async () => { const result = await api('/browser/status'); openModal('Application browser',`<p class="modal-description">${esc(result.message || (result.installed ? 'Camoufox is installed.' : 'Install Camoufox to enable browser assistance.'))}</p><pre class="code">pip install -r requirements-browser.txt
python -m camoufox fetch</pre><p class="hint section-gap">Run these commands in the project’s activated virtual environment.</p>`); return {}; }); break;
  }
});

document.addEventListener('submit', async (event) => {
  const form = event.target; event.preventDefault();
  if (!(form instanceof HTMLFormElement)) return;
  const b = event.submitter || form.querySelector('[type=submit]'), values = formValues(form), id = form.dataset.id;
  if (form.classList.contains('codex-question-form')) { await codexAction(async () => { codex.chat = await api('/codex/chat/respond',{method:'POST',body:{id,answers:values}}); }); return; }
  if (form.classList.contains('answer-form')) { await task(b,() => api(`/auto-apply/applications/${id}/answer`,{method:'POST',body:{answer:values.answer.trim()}})); return; }
  if (form.dataset.bag) {
    const bag = form.dataset.bag, name = values.name.trim(); if (!name) return;
    if (state[bag].some(i => i.name.toLowerCase() === name.toLowerCase())) return toast('That item is already in your list.',true);
    await task(b,() => api(`/${bag}`,{method:'PUT',body:{items:[...state[bag],{name,confirmed:true}]}}),{message:'Added to your confirmed list.'}); return;
  }
  switch(form.id) {
    case 'codex-chat-form': await sendCodexMessage(values.message); break;
    case 'profile-form': await task(b,() => api('/profile',{method:'PUT',body:values}),{message:'Your details are saved.'}); break;
    case 'import-form': {
      await importResume(form,b); break;
    }
    case 'xyz-form': await task(b,() => api(`/items/${id}/xyz`,{method:'PUT',body:{xyz:values.xyz}}),{close:true,message:'Your XYZ wording is saved and used in your resumes.'}); break;
    case 'item-form': await task(b,() => api(id ? `/items/${id}` : '/items',{method:id ? 'PUT' : 'POST',body:values}),{close:true,message:'Experience saved. Review it to add it to your resume.'}); break;
    case 'review-form': {
      const previous = state.items.find(item => item.id === id);
      if (!previous) return toast('This entry no longer exists. Refresh your profile.',true);
      const body = Object.fromEntries(['kind','title','organization','start','end','original'].map(key => [key,previous[key] || '']));
      await task(b,() => api(`/items/${id}`,{method:'PUT',body:{...body,enhanced:values.enhanced,confirmed:true}}),{close:true,message:'Experience confirmed. Your story, in your words.'}); break;
    }
    case 'goldmove-form': {
      const terms = goldTerms();
      if (!terms.length) return toast('Tick the keywords you have first.',true);
      const result = await task(b,() => api('/goldmove/confirm',{method:'POST',body:{terms}}));
      if (result) { gold.status = result; renderGoldmove(true); }
      break;
    }
    case 'auto-apply-rules': await task(b,() => api('/auto-apply/rules',{method:'PUT',body:{min_ats:Number(values.min_ats) || 0}})); break;
    case 'answers-form': {
      const data = new FormData(form), questions = data.getAll('other_question'), replies = data.getAll('other_answer');
      const body = Object.fromEntries(answerFields.map(([name]) => [name,String(values[name] || '').trim()]));
      body.other = questions.map((question,index) => ({question:String(question).trim(),answer:String(replies[index] || '').trim()})).filter(item => item.question && item.answer);
      await task(b,() => api('/auto-apply/answers',{method:'PUT',body}),{message:'Your answers are saved. Auto-apply uses them for every application.'}); break;
    }
    case 'preferences-form': values.remote_only = form.elements.remote_only.checked; await task(b,() => api('/preferences',{method:'PUT',body:values}),{message:'Your preferences are saved.'}); break;
    case 'source-form': {
      values.enabled = form.elements.enabled.checked;
      const items = id ? state.sources.map(s => s.id === id ? {...s,...values} : s) : [...state.sources,{...values,id:crypto.randomUUID()}];
      await task(b,() => api('/sources',{method:'PUT',body:{items}}),{close:true,message:'Your job source is saved.'}); break;
    }
    case 'linkedin-form': {
      if (b?.disabled || !form.reportValidity()) return;
      const customLocation = values.location.trim();
      const regions = customLocation ? [customLocation] : new FormData(form).getAll('region');
      if (!regions.length) return toast('Choose at least one region or enter a custom location.',true);
      const body = {keywords:values.keywords.trim(),regions,remote_only:form.elements.remote_only.checked,limit:Number(values.limit),save:true};
      const result = await task(b,() => api('/linkedin/search',{method:'POST',body}));
      if (!result) break;
      if (result.requires_action) {
        const status = form.querySelector('#linkedin-status');
        const rateLimited = /rate[\s-]*limit|too many requests|429/i.test(result.message || '');
        const title = rateLimited ? 'LinkedIn is limiting requests. Wait before retrying.' : 'LinkedIn needs attention.';
        const nextStep = rateLimited ? 'Wait before selecting Search LinkedIn again. There is no automatic retry.' : 'Complete any requested sign-in or verification in the opened Camoufox browser, then select Search LinkedIn again.';
        status.innerHTML = `<div class="notice section-gap">${icon('info')}<span><strong>${title}</strong><br>${esc(result.message || 'Check the opened LinkedIn browser before continuing.')}<br>${nextStep} Your search details have been kept.</span></div>`;
      } else {
        jobFilter = 'all'; jobQuery = '';
        modal.close();
        if (location.hash === '#jobs') render(); else location.hash = 'jobs';
      }
      break;
    }
    case 'job-form': await task(b,() => api('/jobs',{method:'POST',body:values}),{close:true,message:'Opportunity saved.'}); break;
    case 'settings-form': values.clear_api_key = Boolean(form.elements.clear_api_key?.checked); await task(b,() => api('/settings',{method:'PUT',body:values}),{message:'AI settings saved.'}); break;
  }
});
document.addEventListener('change', async (event) => {
  const input = event.target;
  if (input.matches('input[type=checkbox][data-bag]')) {
    const checked = input.checked; input.disabled = true;
    try { await api(`/${input.dataset.bag}`,{method:'PUT',body:{items:state[input.dataset.bag].map(i => i.id === input.dataset.id ? {...i,confirmed:checked} : i)}}); await refresh(); }
    catch (error) { input.checked = !checked; input.disabled = false; toast(error.message,true); }
  }
  if (input.matches('[data-source]')) {
    const checked = input.checked; input.disabled = true;
    try { await api('/sources',{method:'PUT',body:{items:state.sources.map(s => s.id === input.dataset.source ? {...s,enabled:checked} : s)}}); await refresh(); }
    catch (error) { input.checked = !checked; input.disabled = false; toast(error.message,true); }
  }
  if (input.name === 'track') document.querySelectorAll('.track-card').forEach(card => card.classList.toggle('selected',card.querySelector('input').checked));
  if (input.matches('#source-form select[name="kind"]') && input.value === 'linkedin') {
    const form = input.form;
    form.elements.url.value = LINKEDIN_SEARCH_URL;
    if (!form.elements.name.value.trim()) form.elements.name.value = 'LinkedIn';
  }
  if (input.id === 'ai-provider') {
    const form = input.form, previous = form.dataset.provider;
    providerDrafts[previous] = {base_url:form.elements.base_url?.value || '',model:form.elements.model?.value || ''};
    const defaults = {codex:{base_url:'',model:''},ollama:{base_url:'http://localhost:11434',model:'llama3.2'},compatible:{base_url:'http://localhost:1234/v1',model:''}};
    document.querySelector('#provider-fields').innerHTML = providerFields(input.value,providerDrafts[input.value] || defaults[input.value]);
    form.dataset.provider = input.value;
    startCodexPolling();
  }
});
document.addEventListener('input', event => {
  if (event.target.id === 'job-search') { jobQuery=event.target.value; document.querySelector('#job-list').innerHTML=jobList(); }
  if (event.target.id === 'codex-prompt') { codex.draft = event.target.value; renderCodexPanels(); }
});
document.addEventListener('dragover', event => { const zone=event.target.closest('#upload-zone'); if (zone) { event.preventDefault(); zone.classList.add('drag'); } });
document.addEventListener('dragleave', event => { if (event.target.id === 'upload-zone') event.target.classList.remove('drag'); });
document.addEventListener('drop', event => { const zone = event.target.closest('#upload-zone'); if (!zone) return; event.preventDefault(); zone.classList.remove('drag'); const files = event.dataTransfer.files; if (files.length) { showImport(); document.querySelector('#import-form input[type=file]').files = files; } });
window.addEventListener('hashchange', async () => {
  const navigation = ++navigationVersion;
  if (modal.open) modal.close();
  stopCodexPolling();
  if (currentRoute === 'assistant' && location.hash.slice(1).split('?')[0] !== 'assistant') {
    try {
      const updated = await api('/state');
      if (navigation !== navigationVersion) return;
      state = updated;
    } catch (error) { if (navigation === navigationVersion) toast(error.message,true); }
  }
  if (navigation !== navigationVersion) return;
  render();
  if (location.hash === '#profile?skills') scrollToSkills(); else window.scrollTo(0,0);
});
window.addEventListener('beforeunload', event => { if (busyCount) event.preventDefault(); });
modal.addEventListener('cancel', event => { if (modal.querySelector('#import-form')?.dataset.importing === 'true') event.preventDefault(); });
modal.addEventListener('click', event => { if (event.target === modal && !busyCount) { const bounds = modal.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) modal.close(); } });
try { await refresh(); } catch (error) { app.innerHTML = `<div class="error-box"><div class="empty-icon">${icon('laptop')}</div><h2>Your workspace needs a moment.</h2><p>${esc(error.message)}</p><button class="btn primary" id="reload">Try again</button></div>`; document.querySelector('#reload').onclick=() => location.reload(); }
