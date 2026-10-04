"""Manual offline Chrome UI smoke test; run with .venv/bin/python tests/browser_codex_smoke.py."""
from pathlib import Path
import json, sys
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.db import default_state
state = default_state()
state['settings']['api_key_set'] = False
state['settings'].pop('api_key', None)
chat = {'messages': [], 'pending': [], 'busy': False, 'error': '', 'conversation_id': 'demo'}
status = {'available': True, 'authenticated': False, 'login_pending': False, 'message': 'Sign in to use Codex.', 'models': []}
requests = []
responses = []
errors = []
login_url = 'https://auth.openai.com/authorize?client_id=test'
login_race = False
base = 'http://putmeto.test'

def route_request(route):
    global chat
    request = route.request
    path = urlsplit(request.url).path
    requests.append((request.method, path))
    payload = request.post_data_json if request.post_data else {}
    if path == '/api/state': body = state
    elif path == '/api/codex/status': body = status
    elif path == '/api/codex/login':
        status['login_pending'] = not login_race
        if login_race: status['authenticated'] = True
        body = {'auth_url': None if login_race else login_url, 'login_id': 'login', 'message': 'Finish signing in.'}
    elif path == '/api/codex/chat': body = chat
    elif path == '/api/codex/chat/message':
        chat = {'messages': chat['messages'] + [{'id': 'm1', 'role': 'user', 'content': payload['message']}, {'id': 't1', 'role': 'tool', 'content': 'Reading your saved jobs…'}], 'pending': [], 'busy': True, 'error': ''}
        body = chat
    elif path == '/api/codex/chat/respond':
        responses.append(payload)
        chat['pending'] = []
        body = chat
    elif path == '/api/codex/chat/cancel':
        chat['busy'] = False
        chat['pending'] = []
        body = chat
    elif path == '/api/codex/chat/reset':
        chat = {'messages': [], 'pending': [], 'busy': False, 'error': ''}
        body = chat
    elif path == '/api/settings':
        state['settings'].update(payload)
        body = {'message': 'Saved.'}
    elif path == '/api/settings/test': body = {'message': 'Connected.'}
    elif path == '/api/job-matching': body = {"state": "idle", "message": "", "done": 0, "total": 0, "model": "", "effort": "", "running": False, "waiting": 0}
    elif path == '/api/goldmove': body = {"state": "idle", "message": "", "done": 0, "total": 0, "running": False, "waiting": 0, "eligible": 0, "checked": 0, "dismissed": 0, "candidates": []}
    elif path == '/api/master-resume': body = {"state": "idle", "message": "", "model": "", "effort": "", "started_at": "", "running": False, "has_cv": False, "built": None}
    elif path == '/api/linkedin/continuous': body = {"running": False, "state": "stopped", "message": "Not running.", "found": 0, "skipped": 0, "next_at": "", "pages_last_hour": 0, "pages_today": 0}
    elif path.startswith('/api/'):
        raise AssertionError(f'Unexpected API request {path}')
    else:
        filename = ROOT / 'static' / (path.rsplit('/', 1)[-1] or 'index.html')
        if not filename.exists(): route.fulfill(status=404); return
        route.fulfill(body=filename.read_bytes(), content_type={'js':'text/javascript','css':'text/css','html':'text/html','svg':'image/svg+xml'}[filename.suffix[1:]])
        return
    route.fulfill(body=json.dumps(body), content_type='application/json')

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.route('**/*', route_request)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('window.openedURLs=[]; window.open=(url)=>{window.openedURLs.push(url);return null}')
    page.goto(base + '/#assistant')
    expect(page.locator('#codex-connection')).to_contain_text('Sign in to ChatGPT')
    expect(page.locator('#codex-send')).to_be_disabled()
    page.get_by_role('button', name='Sign in with ChatGPT').click()
    expect(page.get_by_role('link', name='Continue ChatGPT sign-in')).to_be_visible()
    assert page.evaluate('window.openedURLs[0]') == login_url
    status.update(authenticated=True, login_pending=False, email='applicant@example.test', plan='Plus', message='Connected to ChatGPT.')
    expect(page.locator('#codex-connection')).to_contain_text('Connected with ChatGPT', timeout=6000)
    page.get_by_role('button', name='Show my saved jobs', exact=True).click()
    expect(page.locator('#codex-messages')).to_contain_text('Reading your saved jobs…')
    expect(page.get_by_role('button', name='Stop', exact=True)).to_be_enabled()
    prompt = page.locator('#codex-prompt')
    prompt.fill('Draft preserved while Codex works')
    chat['messages'].append({'id':'a1','role':'assistant','content':'<img src=x onerror="window.bad=true">\nA saved job in Europe.'})
    expect(page.locator('#codex-messages')).to_contain_text('A saved job in Europe.', timeout=6000)
    expect(prompt).to_have_value('Draft preserved while Codex works')
    expect(prompt).to_be_focused()
    assert page.locator('#codex-messages img').count() == 0
    assert page.evaluate('window.bad') is None
    chat['pending'] = [{'id':'question-1','kind':'question','title':'Choose a location','description':'Which region?','questions':[{'id':'region','question':'Where should I search?','options':[{'label':'Europe','description':'European roles'},{'label':'United States','description':'US roles'}]}]}]
    expect(page.get_by_label('Where should I search?')).to_be_visible(timeout=6000)
    page.get_by_role('button', name='Europe', exact=True).click()
    expect(page.get_by_label('Where should I search?')).to_have_value('Europe')
    page.wait_for_timeout(1500)
    expect(page.get_by_label('Where should I search?')).to_have_value('Europe')
    page.get_by_role('button', name='Send answers').click()
    expect(page.locator('.chat-request')).to_have_count(0)
    assert responses[-1] == {'id':'question-1','answers':{'region':'Europe'}}
    chat['pending'] = [{'id':'approval-1','kind':'approval','title':'Save this job?','description':'This adds one job to your workspace.'}]
    expect(page.get_by_role('button', name='Decline', exact=True)).to_be_visible(timeout=6000)
    page.get_by_role('button', name='Decline', exact=True).click()
    expect(page.locator('.chat-request')).to_have_count(0)
    assert responses[-1] == {'id':'approval-1','approved':False}
    page.get_by_role('button', name='Stop', exact=True).click()
    expect(page.get_by_role('button', name='Stop', exact=True)).to_be_disabled()
    page.get_by_role('button', name='New chat', exact=True).click()
    expect(page.locator('#codex-messages')).to_contain_text('What would you like to work on?')
    expect(prompt).to_have_value('Draft preserved while Codex works')
    page.locator('.nav a[href="#settings"]').click()
    form = page.locator('#settings-form')
    form.locator('[name=provider]').select_option('ollama')
    form.locator('[name=model]').fill('saved-local-model')
    form.locator('[name=base_url]').fill('http://localhost:11434')
    form.locator('[name=provider]').select_option('compatible')
    form.locator('[name=model]').fill('custom-model')
    form.locator('[name=base_url]').fill('http://localhost:1234/v1')
    form.locator('[name=provider]').select_option('codex')
    expect(form.locator('[name=model]')).to_have_value('')
    expect(form.locator('[name=api_key]')).to_have_count(0)
    expect(form.locator('[name=base_url]')).to_have_count(0)
    assert form.evaluate('form=>form.reportValidity()')
    form.locator('[name=provider]').select_option('ollama')
    expect(form.locator('[name=model]')).to_have_value('saved-local-model')
    form.locator('[name=provider]').select_option('compatible')
    expect(form.locator('[name=model]')).to_have_value('custom-model')
    form.locator('[name=provider]').select_option('codex')
    form.get_by_role('button', name='Save settings').click()
    expect(page.locator('.toast')).to_contain_text('Saved.')
    assert state['settings']['provider'] == 'codex'
    assert state['settings']['model'] == ''
    page.locator('.nav a[href="#assistant"]').click()
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('#codex-prompt')).to_have_value('Draft preserved while Codex works')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
    page.screenshot(path='/private/tmp/putmeto-codex-mobile.png', full_page=True)
    page.get_by_role('button', name='Send', exact=True).click()
    expect(page.get_by_role('button', name='Stop', exact=True)).to_be_enabled()
    state['jobs'].append({'id':'new-job','title':'Job saved by Codex','company':'Fixture Company','location':'Europe','status':'saved','match_score':0,'description':'Added from chat','source':'LinkedIn','url':'https://www.linkedin.com/jobs/view/123456/'})
    page.get_by_role('button', name='Toggle navigation').click()
    page.locator('.nav a[href="#jobs"]').click()
    expect(page.locator('#job-list')).to_contain_text('Job saved by Codex')
    count = len([request for request in requests if request[1].startswith('/api/codex')])
    page.wait_for_timeout(2000)
    assert count == len([request for request in requests if request[1].startswith('/api/codex')]), 'Polling continued after navigation'
    status.update(authenticated=False, login_pending=False)
    login_race = True
    chat['busy'] = False
    page.get_by_role('button', name='Toggle navigation').click()
    page.locator('.nav a[href="#assistant"]').click()
    expect(page.get_by_role('button', name='Sign in with ChatGPT')).to_be_visible()
    page.get_by_role('button', name='Sign in with ChatGPT').click()
    expect(page.locator('#codex-connection')).to_contain_text('Connected with ChatGPT')
    expect(page.locator('#codex-error')).to_be_hidden()
    assert not errors, errors
    browser.close()
print('Codex browser UI checks passed: authentication, chat, polling, draft/focus, safe text, questions, approval, cancel/reset, provider switching, mobile, and route cleanup.')
