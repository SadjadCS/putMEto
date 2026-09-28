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
  {label:'Build your profile',description:'Import your CV and review your experience.',done:confirmed(state.items).length > 0,to:'profile'},
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
function profile() {
  const p = state.profile, pending = state.items.filter(i => !i.confirmed).length;
  return `${heading('The story so far.','Your experience is the foundation of every application.',button('Preview resume','resume','eye') + button('Add experience','add-item','plus','primary'),'YOUR PROFILE')}
  <div class="upload-zone" id="upload-zone"><div class="upload-copy"><div class="empty-icon">${icon('upload')}</div><div><h3>A fresh start from your existing CV</h3><p>Drop a PDF or text file here. We’ll pull out the important parts.</p></div></div>${button('Import your CV','import','upload')}</div>
  <section class="panel"><div class="panel-head"><h2>The essentials</h2>${badge('Saved locally','','shield')}</div><form id="profile-form" class="form-pad" style="padding-top:7px"><div class="form-grid">${field('Full name','name',p.name,{required:true})}${field('Professional headline','headline',p.headline,{placeholder:'What you do, in a few words'})}${field('Email address','email',p.email,{type:'email'})}${field('Phone number','phone',p.phone,{type:'tel'})}${field('Location','location',p.location)}${field('Portfolio or website','website',p.website,{type:'url',placeholder:'https://…'})}${textarea('Profile summary','summary',p.summary)}</div><div class="form-footer"><span class="hint">These details appear on your resume.</span><button type="submit" class="btn primary">Save details ${icon('check')}</button></div></form></section>
  <section class="section-gap"><div class="section-title"><div><h2>Your experience & projects</h2><p>Broader language. Same achievements. Review every suggestion.</p></div>${pending ? badge(`${pending} awaiting review`,'amber','clock') : badge('All caught up','','check')}</div><div class="stack">${state.items.length ? state.items.map(item => `<article class="item-card"><div class="item-head"><div class="item-title"><div class="item-kind">${icon(({experience:'briefcase',project:'code',education:'book',publication:'file'})[item.kind])}</div><div><h3>${esc(item.title)}</h3><p class="meta">${esc(item.organization)}${item.start || item.end ? ` · ${esc(item.start)} – ${esc(item.end || 'Present')}` : ''}</p></div></div>${badge(item.confirmed ? 'Confirmed' : 'Needs review',item.confirmed ? '' : 'amber',item.confirmed ? 'check' : 'clock')}</div><p class="item-description">${esc(item.enhanced || item.original)}</p><div class="item-footer"><span class="review-note">${item.confirmed ? 'Ready to include in your resume' : 'Review the wording before it joins your profile'}</span><div class="actions">${button('','delete-item','trash','text small',`data-id="${esc(item.id)}" aria-label="Delete ${esc(item.title)}"`)}${button('Edit','edit-item','edit','text small',`data-id="${esc(item.id)}"`)}${button(item.confirmed ? 'Review' : 'Review & confirm','review-item','check',item.confirmed ? 'small' : 'small primary',`data-id="${esc(item.id)}"`)}</div></div></article>`).join('') : `<div class="panel">${empty('briefcase','Your experience belongs here.','Import a CV or add your work, projects, education, and publications yourself.',button('Add your first item','add-item','plus','primary'))}</div>`}</div></section>
  <section class="panel section-gap" id="technical-skills"><div class="panel-head skills-panel-head"><div><h2>Your technical skills</h2><p>Review the methods, tools, and technologies you can use.</p></div><div class="actions">${button('Import skills from CV','import-skills','upload','small')}${button('Suggest skills','suggest-skills','sparkles','small')}</div></div><div class="panel-body">${skillGroups()}<form class="inline-form" data-bag="skills"><label class="sr-only" for="add-skill">Add a technical skill</label><input id="add-skill" name="name" placeholder="Add a skill, e.g. Python or data analysis" required maxlength="100"/><button class="btn" type="submit">${icon('plus')}Add skill</button></form></div></section>`;
}
function skillCard(skill) {
  const related = skill.support === 'related';
  const kind = ({technique:'Technique',language:'Language',framework:'Framework',database:'Database',platform:'Platform',tool:'Tool',other:'Technical skill'})[skill.kind] || 'Technical skill';
  const source = related ? 'Explore and confirm' : skill.origin === 'resume' ? 'Listed in your CV' : skill.support === 'supported' ? 'From your background' : skill.origin === 'manual' ? 'Added by you' : 'Review skill';
  const aliases = Array.isArray(skill.aliases) ? skill.aliases.filter(alias => alias && alias.toLowerCase() !== skill.name.toLowerCase()) : [];
  return `<article class="skill-card ${skill.confirmed ? 'confirmed' : related ? 'related' : ''}" data-skill-id="${esc(skill.id)}"><div class="skill-card-top"><div><span class="skill-kind">${esc(kind)}</span><h4>${esc(skill.name)}</h4></div>${badge(skill.confirmed ? 'Confirmed' : source,skill.confirmed ? '' : related ? 'amber' : 'gray',skill.confirmed ? 'check' : '')}</div>${aliases.length ? `<p class="skill-aliases">Also called: ${aliases.map(esc).join(', ')}</p>` : ''}${skill.rationale ? `<p class="skill-rationale">${esc(skill.rationale)}</p>` : ''}${skill.evidence ? `<div class="skill-evidence"><span>${related ? 'Related experience' : 'Supporting context'}</span><p>${esc(skill.evidence)}</p></div>` : ''}<div class="skill-card-footer"><label class="check-label"><input type="checkbox" data-bag="skills" data-id="${esc(skill.id)}" ${skill.confirmed ? 'checked' : ''} aria-label="Confirm ${esc(skill.name)}"/>${skill.confirmed ? 'Confirmed for my profile' : 'I have this skill'}</label>${button(skill.confirmed ? 'Remove' : 'Dismiss','remove-chip','x','text small',`data-bag="skills" data-id="${esc(skill.id)}" aria-label="${skill.confirmed ? 'Remove' : 'Dismiss'} ${esc(skill.name)}"`)}</div></article>`;
}
function skillGroups() {
  if (!state.skills.length) return `<div class="skills-empty"><h3>Bring all of your skills into view.</h3><p>Import the skills listed in your CV, add one yourself, or ask for techniques and tools related to your experience.</p><p class="hint">Confirm each skill before it becomes available for your resumes and job matching.</p></div>`;
  const groups = [
    ['supported','Skills from your background','Check the source context and confirm the skills you know.',skill => !skill.confirmed && skill.support !== 'related' && (skill.support === 'supported' || skill.origin === 'resume')],
    ['related','Related skills to explore','These options may fit your experience. Confirm only the skills you actually have.',skill => !skill.confirmed && skill.support === 'related'],
    ['review','Other skills to review','Review these saved skills and confirm the ones you know.',skill => !skill.confirmed && !['supported','related'].includes(skill.support) && skill.origin !== 'resume'],
    ['confirmed','Your confirmed skills','Available for your resumes and job matching.',skill => skill.confirmed],
  ];
  return groups.map(([key,title,description,include]) => {
    const skills = state.skills.filter(include);
    return skills.length ? `<section class="skill-group" data-skill-group="${key}"><div class="skill-group-head"><div><h3>${title}</h3><p>${description}</p></div>${badge(`${skills.length}`,'gray')}</div><div class="skill-grid">${skills.map(skillCard).join('')}</div></section>` : '';
  }).join('');
}
function scrollToSkills() { document.querySelector('#technical-skills')?.scrollIntoView({block:'start'}); }
function reviewSkills() {
  if (location.hash === '#profile?skills') { render(); scrollToSkills(); }
  else location.hash = 'profile?skills';
}
function chips(bag) { return state[bag].length ? `<div class="chip-list">${state[bag].map(item => `<span class="chip ${item.confirmed ? 'confirmed' : ''}"><label><input type="checkbox" data-bag="${bag}" data-id="${esc(item.id)}" ${item.confirmed ? 'checked' : ''} aria-label="Confirm ${esc(item.name)}"/>${esc(item.name)}</label><button data-action="remove-chip" data-bag="${bag}" data-id="${esc(item.id)}" aria-label="Remove ${esc(item.name)}">${icon('x')}</button></span>`).join('')}</div>` : `<p class="hint">Add roles you’re interested in, or generate suggestions from your confirmed profile.</p>`; }
function preferences() {
  const p = state.preferences;
  return `${heading('Where do you want to go?','Find the roles that fit your experience and your ambitions.','','YOUR DIRECTION')}
  <form id="preferences-form"><div class="section-title"><h2>Choose your path</h2></div><div class="track-options">${[['industry','Industry','Build, create, and grow with a company.','briefcase'],['academia','Academia','Research, teach, and advance your field.','book']].map(([value,title,desc,glyph]) => `<label class="track-card ${p.track === value ? 'selected' : ''}"><input type="radio" name="track" value="${value}" ${p.track === value ? 'checked' : ''}/>${icon(glyph)}<div><h3>${title}</h3><p>${desc}</p></div></label>`).join('')}</div><section class="panel"><div class="form-pad"><div class="form-grid">${field('Preferred location','location',p.location,{full:true,placeholder:'City, country, or leave blank for anywhere'})}<label class="check-label full"><input type="checkbox" name="remote_only" ${p.remote_only ? 'checked' : ''}/> Only show remote opportunities</label></div><div class="form-footer"><button class="btn primary" type="submit">Save preferences ${icon('check')}</button></div></div></section></form>
  <section class="panel section-gap"><div class="panel-head"><div><h2>Roles that feel like you</h2><p>Check a role to include it in your search.</p></div>${button('Suggest roles','suggest-positions','sparkles','small')}</div><div class="panel-body">${chips('positions')}<form class="inline-form" data-bag="positions"><label class="sr-only" for="add-role">Add a job title</label><input id="add-role" name="name" placeholder="${p.track === 'academia' ? 'e.g. Research Fellow, Postdoctoral Researcher' : 'e.g. Software Engineer, Product Designer'}" required maxlength="100"/><button class="btn" type="submit">${icon('plus')}Add role</button></form></div></section><div class="notice section-gap">${icon('info')}<span>Suggestions are a starting point. Confirm the titles you would actually like to apply for. Your search uses these roles alongside your technical skills.</span></div>`;
}
function sources() {
  const notes = {remotive:'Remote opportunities from Remotive’s public job feed.',greenhouse:'Search the public job board of a company on Greenhouse.',lever:'Explore openings from a company’s Lever career page.',linkedin:'Browse a batch of jobs across all titles in the United States and Europe. Camoufox keeps your LinkedIn sign-in for future searches.',camoufox:'Visit a career page with Camoufox and read JobPosting data.'};
  return `${heading('Good opportunities start somewhere.','Choose the sources you want in your search.',button('Add a source','add-source','plus','primary'),'YOUR JOB SOURCES')}<div class="notice">${icon('globe')}<span>Company boards need a careers URL. LinkedIn opens a visible browser; custom pages need structured job listings. You can also search LinkedIn directly or add any job manually.</span></div><div class="source-grid">${state.sources.map(source => `<article class="source-card ${source.enabled ? 'enabled' : ''}"><div class="source-card-head"><div class="source-logo ${['greenhouse','lever'].includes(source.kind) ? 'green' : ''}">${esc(source.name[0] || 'S')}</div><input class="toggle" type="checkbox" data-source="${esc(source.id)}" ${source.enabled ? 'checked' : ''} aria-label="Enable ${esc(source.name)}"/></div><h3>${esc(source.name)}</h3><p>${notes[source.kind] || notes.camoufox}</p><div class="source-url">${esc(source.url || 'Add a company board URL to get started')}</div>${source.kind === 'linkedin' ? `<div class="actions">${button('Search LinkedIn','search-linkedin','search','small')}</div>` : ''}<div class="item-footer">${badge(['camoufox','linkedin'].includes(source.kind) ? 'Browser discovery' : 'Public job feed','gray',['camoufox','linkedin'].includes(source.kind) ? 'globe' : 'refresh')}<div class="actions">${button('Edit','edit-source','edit','text small',`data-id="${esc(source.id)}"`)}${button('','delete-source','trash','text small',`data-id="${esc(source.id)}" aria-label="Delete ${esc(source.name)}"`)}</div></div></article>`).join('')}</div><div class="section-gap actions">${link('Discover opportunities','jobs','arrow','primary')}<span class="hint">${state.sources.filter(s => s.enabled).length} sources selected for your next search</span></div>`;
}
function jobCard(job) { return `<article class="job-card"><div class="company-mark">${initial(job.company)}</div><div class="job-body"><div class="job-top"><div><h3>${esc(job.title)}</h3><p class="job-company">${esc(job.company)} · ${esc(job.location || 'Location not specified')}</p></div><span class="match" title="Keyword overlap with your confirmed skills and roles">${icon('sparkles')}${Number(job.match_score) || 0}% match</span></div><div class="job-tags">${badge(job.job_type || 'Opportunity','gray')}${job.salary ? badge(job.salary,'gray') : ''}${badge(job.status || 'discovered',job.status === 'applied' ? '' : 'gray')}${(job.matched_skills || []).slice(0,3).map(s => badge(s)).join('')}</div><div class="job-bottom"><span>via ${esc(job.source)}${job.posted_at ? ` · ${dateLabel(job.posted_at)}` : ''}</span><div class="actions">${job.status === 'discovered' ? button('Save','save-job','bookmark','small',`data-id="${esc(job.id)}"`) : ''}${button('View opportunity','view-job','arrow','small',`data-id="${esc(job.id)}"`)}</div></div></div></article>`; }
function filteredJobs() { return state.jobs.filter(j => (jobFilter === 'all' ? j.status !== 'archived' : jobFilter === 'prepared' ? Boolean(j.resume) && j.status !== 'archived' : j.status === jobFilter) && `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(jobQuery.toLowerCase())).sort((a,b) => b.match_score-a.match_score); }
function jobList() { const jobs = filteredJobs(); return jobs.length ? jobs.map(jobCard).join('') : `<div class="panel">${empty('search',jobQuery ? 'No matches for that search.' : 'Make room for possibility.',jobQuery ? 'Try another title, company, or location.' : 'Confirm your preferred roles, choose your sources, then discover jobs that fit.',jobQuery ? '' : button('Discover jobs','discover','sparkles','primary'))}</div>`; }
function jobs() { return `${heading('A world of possibilities.','Opportunities matched to the experience you’ve confirmed.',button('Add a job','add-job','plus')+button('Search LinkedIn','search-linkedin','search')+button('Discover jobs','discover','sparkles','primary'),'YOUR NEXT MOVE')}<div class="toolbar"><div class="tabs" role="group" aria-label="Filter jobs">${[['all','All opportunities'],['saved','Saved'],['prepared','Tailored'],['archived','Archived']].map(([key,label]) => `<button class="tab ${jobFilter === key ? 'active' : ''}" data-action="filter-jobs" data-filter="${key}" aria-pressed="${jobFilter === key}">${label}</button>`).join('')}</div><div class="search">${icon('search')}<label class="sr-only" for="job-search">Search jobs</label><input id="job-search" value="${esc(jobQuery)}" placeholder="Search your opportunities…"/></div></div><div class="stack" id="job-list">${jobList()}</div><p class="hint section-gap">Match scores reflect keyword overlap with your confirmed roles and skills. Always review the full job description and eligibility.</p>`; }
function applications() { return `${heading('Every step counts.','Keep track of the applications you’re moving forward.','','YOUR APPLICATIONS')}<div class="stats">${[['In progress',state.applications.filter(a => a.status !== 'applied').length,'clock','A little preparation goes a long way'],['Submitted',state.applications.filter(a => a.status === 'applied').length,'send','Confirmed by you'],['Tailored resumes',state.jobs.filter(j => j.resume).length,'file','A considered introduction'],['Saved for later',state.jobs.filter(j => j.status === 'saved').length,'bookmark','Worth coming back to']].map(([label,value,glyph,note]) => `<div class="stat"><div class="stat-top">${label}${icon(glyph)}</div><div class="stat-number">${String(value).padStart(2,'0')}</div><div class="stat-bottom">${note}</div></div>`).join('')}</div><section class="panel">${state.applications.length ? [...state.applications].reverse().map(a => { const job = state.jobs.find(j => j.id === a.job_id); return `<article class="application-row"><div class="application-info"><div class="company-mark">${initial(job?.company)}</div><div><h3>${esc(job?.title || 'Job application')}</h3><p class="meta">${esc(job?.company)} · Started ${dateLabel(a.created_at)}</p>${a.note ? `<p class="meta">${esc(a.note)}</p>` : ''}</div></div><div class="application-right">${badge(a.status === 'applied' ? 'Submitted' : 'In progress',a.status === 'applied' ? '' : 'amber',a.status === 'applied' ? 'check' : 'clock')}${a.status !== 'applied' ? button('Mark submitted','confirm-application','check','small',`data-id="${esc(a.id)}"`) : ''}${button('View','view-job','arrow','text small',`data-id="${esc(a.job_id)}"`)}</div></article>`; }).join('') : empty('send','Your story is ready for its next audience.','When you open an application from a tailored job, it will appear here.',link('Explore opportunities','jobs','arrow','primary'))}</section>`; }
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
  openModal(skillsOnly ? 'Bring your CV skills into your profile' : 'Your experience, imported.',`<p class="modal-description">${skillsOnly ? 'Upload your CV to recover its technical skills and methods. Review and confirm the extracted skills below your experience.' : 'Choose a text-based PDF, Word (.docx), or plain text CV. AI will extract your experience and suggest broader wording for you to review.'}</p><form id="import-form" data-mode="${skillsOnly ? 'skills' : 'resume'}"><label>Your CV<input type="file" name="file" accept=".pdf,.txt,.docx,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required aria-describedby="import-error"/></label><div class="notice section-gap">${icon('info')}<span>${skillsOnly ? 'This import adds skill candidates for your review and keeps your existing experience entries. Choose a text-based PDF, Word (.docx), or plain text CV.' : 'New items are added as drafts. Existing experience is kept. Scanned PDFs need text recognition before import. Reading and extracting a CV can take a few minutes.'}</span></div><p id="import-status" class="import-status section-gap" role="status" aria-live="polite" hidden></p><p id="import-error" class="import-error section-gap" role="alert" tabindex="-1" hidden></p><div class="form-footer"><button class="btn primary" type="submit">${icon('sparkles')}${skillsOnly ? 'Import skills for review' : 'Import & extract'}</button></div></form>`);
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
    if (!file.size) throw new Error('This file is empty. Choose a PDF, Word (.docx), or text CV with readable content.');
    const header = new TextDecoder().decode(await file.slice(0,1024).arrayBuffer());
    const name = file.name.trim().toLowerCase(), type = file.type.toLowerCase();
    const isPDF = header.includes('%PDF-');
    const docxType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    if (/\.doc$/.test(name) && !isPDF && type !== docxType) {
      throw new Error('This is an older Word document. Save as .docx or PDF, then upload again.');
    }
    if (!isPDF && !/\.(pdf|txt|docx)$/.test(name) && !['application/pdf','text/plain',docxType].includes(type)) {
      throw new Error('Choose a PDF, Word (.docx), or plain text CV.');
    }
    const updateStatus = () => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      statusBox.textContent = `${skillsOnly ? 'Extracting technical skills from your CV' : 'Reading your CV and preparing draft entries'}… ${seconds}s elapsed. Larger resumes can take a few minutes. Keep this window open.`;
    };
    updateStatus(); timer = setInterval(updateStatus,1000);
    // The server bounds AI extraction to five minutes. Keep a final network
    // deadline too, so a lost connection cannot leave this dialog busy forever.
    timeout = setTimeout(() => controller.abort(),330000);
    const data = new FormData(); data.append('file',file);
    const result = await api(skillsOnly ? '/import/skills' : '/import',{method:'POST',body:data,signal:controller.signal});
    saved = true; statusBox.textContent = skillsOnly ? 'Imported. Opening your skills for review…' : 'Imported. Opening your draft entries…';
    // Fetch the saved data before closing: a failed refresh must not suggest
    // retrying the upload and accidentally append duplicate entries.
    state = await api('/state',{signal:controller.signal});
    modal.close(); location.hash = skillsOnly ? 'profile?skills' : 'profile'; render();
    if (skillsOnly) scrollToSkills();
    toast(result.message || (skillsOnly ? 'Skills imported. Review and confirm the ones you know.' : 'Resume imported. Review your draft entries.'));
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
function showItem(item) { openModal(item ? 'Edit your experience' : 'Add to your story',`<p class="modal-description">Describe what you actually did. AI will suggest broader language without adding new achievements or qualifications.</p><form id="item-form" data-id="${esc(item?.id || '')}"><div class="form-grid"><label class="full">Type<select name="kind">${[['experience','Work experience'],['project','Project'],['education','Education'],['publication','Publication']].map(([value,label]) => `<option value="${value}" ${item?.kind === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${field('Title','title',item?.title,{required:true,placeholder:'Role, project, degree, or publication'})}${field('Organization','organization',item?.organization)}${field('Start date','start',item?.start,{placeholder:'e.g. Jan 2022'})}${field('End date','end',item?.end,{placeholder:'e.g. Present'})}${textarea('What did you do?','original',item?.original,true)}</div><div class="form-footer"><span class="hint">Saved as a draft for your review.</span><button class="btn primary" type="submit">${icon('sparkles')}Save & enhance</button></div></form>`); }
function showReview(item) { openModal('Your experience. Your words.',`<p class="modal-description">Check that the suggestion accurately represents your work. You can edit the wording before confirming it.</p><form id="review-form" data-id="${esc(item.id)}"><div class="compare"><div><h3>Original description</h3><p>${esc(item.original)}</p></div><div><h3>Suggested description</h3><label><span class="sr-only">Reviewed description</span><textarea name="enhanced" required>${esc(item.enhanced || item.original)}</textarea></label></div></div><label class="check-label confirm-line"><input type="checkbox" required/> This accurately describes my experience.</label><div class="form-footer">${button('Rephrase with AI','enhance-item','sparkles','',`data-id="${esc(item.id)}"`)}<button class="btn primary" type="submit">${icon('check')}Confirm experience</button></div></form>`); }
function showSource(source) { openModal(source ? 'Edit job source' : 'Find another source',`<p class="modal-description">Connect a company’s public career board, LinkedIn browser search, or a page that publishes structured JobPosting data.</p><form id="source-form" data-id="${esc(source?.id || '')}"><div class="form-grid">${field('Source name','name',source?.name,{full:true,required:true,placeholder:'e.g. My favourite company'})}<label class="full">Source type<select name="kind">${[['greenhouse','Greenhouse company board'],['lever','Lever company board'],['linkedin','LinkedIn jobs (Camoufox)'],['camoufox','Custom careers page (Camoufox)'],['remotive','Remotive remote jobs']].map(([value,label]) => `<option value="${value}" ${source?.kind === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${field('Career board URL','url',source?.kind === 'linkedin' ? LINKEDIN_SEARCH_URL : source?.url,{type:'url',required:true,full:true,placeholder:'https://boards.greenhouse.io/company'})}<label class="check-label full"><input type="checkbox" name="enabled" ${!source || source.enabled ? 'checked' : ''}/> Include this source in my search</label></div><div class="form-footer"><button class="btn primary" type="submit">${icon('check')}Save source</button></div></form>`); }
function showLinkedIn() {
  openModal('Search LinkedIn',`<p class="modal-description">Explore a batch of jobs across any title in the United States and Europe, or add keywords to narrow your search. This search is independent of your saved profile and role preferences.</p><form id="linkedin-form"><div class="form-grid">${field('Keywords or job title (optional)','keywords','',{full:true,placeholder:'Any job title'})}<div class="full"><p class="hint" id="linkedin-regions-label">Search regions</p><div class="actions" role="group" aria-labelledby="linkedin-regions-label"><label class="check-label"><input type="checkbox" name="region" value="United States" checked/> United States</label><label class="check-label"><input type="checkbox" name="region" value="Europe" checked/> Europe</label></div></div>${field('Custom location (optional)','location','',{full:true,placeholder:'Overrides the selected regions, e.g. London'})}<label>Maximum jobs per region<input type="number" name="limit" min="1" max="25" step="1" value="10" required/></label><label class="check-label"><input type="checkbox" name="remote_only"/> Only remote opportunities</label></div><div class="notice section-gap">${icon('globe')}<span>Your LinkedIn sign-in is kept in a local browser profile for future searches. Complete any sign-in or verification in the opened browser. Results are a limited batch and some descriptions may be partial.</span></div><div id="linkedin-status" role="status" aria-live="polite"></div><div class="form-footer"><button class="btn primary" type="submit">${icon('search')}Search LinkedIn</button></div></form>`);
}
function showAddJob() { openModal('Found something interesting?',`<p class="modal-description">Save a job from anywhere. Paste the full description so we can focus your resume on the right experience.</p><form id="job-form"><div class="form-grid">${field('Job title','title','',{required:true})}${field('Company','company','',{required:true})}${field('Location','location','',{placeholder:'e.g. Remote, New York'})}${field('Salary (optional)','salary')}${field('Job URL','url','',{type:'url',required:true,full:true})}${textarea('Full job description','description','',true)}</div><div class="form-footer"><button class="btn primary" type="submit">${icon('bookmark')}Save opportunity</button></div></form>`); }
function showJob(job) {
  if (!job) return toast('That job is no longer available in your workspace.', true);
  openModal(esc(job.title),`<p class="modal-description">${esc(job.company)} · ${esc(job.location || 'Location not specified')}</p><div class="actions">${badge(`${job.match_score || 0}% keyword match`,'','sparkles')}${badge(job.status,'gray')}${external('Original listing',job.url,'up','text small')}</div><div class="detail-grid"><div><small>Source</small><p>${esc(job.source)}</p></div><div><small>Compensation</small><p>${esc(job.salary || 'Not specified')}</p></div></div>${job.resume ? `<div class="notice">${icon('check')}<span>Your tailored resume is ready. Review it before opening the application.</span></div><div class="actions" style="margin-bottom:20px"><a class="btn small" href="/api/jobs/${encodeURIComponent(job.id)}/resume" target="_blank" rel="noopener">${icon('eye')}Preview resume</a><a class="btn small" href="/api/jobs/${encodeURIComponent(job.id)}/resume.pdf" target="_blank" rel="noopener">${icon('download')}Download PDF</a></div>` : ''}<div class="description">${esc(job.description)}</div><div class="job-skills-prompt"><p>Review techniques and tools relevant to this opportunity.</p>${button('Suggest relevant skills','suggest-job-skills','sparkles','small',`data-id="${esc(job.id)}"`)}<p id="job-skills-status" class="import-error" role="alert" hidden></p></div><div class="form-footer">${button(job.status === 'archived' ? 'Restore' : 'Archive',job.status === 'archived' ? 'save-job' : 'archive-job','bookmark','text small',`data-id="${esc(job.id)}"`)}${button(job.resume ? 'Re-tailor resume' : 'Tailor my resume','tailor-job','sparkles',job.resume ? '' : 'primary',`data-id="${esc(job.id)}"`)}${job.resume ? button('Open application','apply-job','up','primary',`data-id="${esc(job.id)}"`) : ''}</div>`);
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
    case 'suggest-positions': await task(b,() => api('/suggestions/positions',{method:'POST'}),{message:'Role suggestions are ready for your review.'}); break;
    case 'remove-chip': await task(b,() => api(`/${b.dataset.bag}`,{method:'PUT',body:{items:state[b.dataset.bag].filter(i => i.id !== id)}})); break;
    case 'add-source': showSource(); break;
    case 'edit-source': showSource(state.sources.find(s => s.id === id)); break;
    case 'delete-source': confirmDialog('Remove this source?','Saved jobs from this source will remain in your workspace.','delete-source-confirmed',id,'Remove source','danger'); break;
    case 'delete-source-confirmed': await task(b,() => api('/sources',{method:'PUT',body:{items:state.sources.filter(s => s.id !== id)}}),{close:true,message:'Source removed.'}); break;
    case 'discover': await task(b,() => api('/jobs/discover',{method:'POST'})); break;
    case 'search-linkedin': showLinkedIn(); break;
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
    case 'item-form': await task(b,() => api(id ? `/items/${id}` : '/items',{method:id ? 'PUT' : 'POST',body:values}),{close:true,message:'Experience saved. Review it to add it to your resume.'}); break;
    case 'review-form': {
      const previous = state.items.find(item => item.id === id);
      if (!previous) return toast('This entry no longer exists. Refresh your profile.',true);
      const body = Object.fromEntries(['kind','title','organization','start','end','original'].map(key => [key,previous[key] || '']));
      await task(b,() => api(`/items/${id}`,{method:'PUT',body:{...body,enhanced:values.enhanced,confirmed:true}}),{close:true,message:'Experience confirmed. Your story, in your words.'}); break;
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
