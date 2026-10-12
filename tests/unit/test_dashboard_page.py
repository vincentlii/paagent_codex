import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from pa_agent.dashboard_page import DASHBOARD_HTML


def test_recent_runs_filter_preserves_status_details_and_read_only_behavior():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            self.ids.extend(value for key, value in attrs if key == "id")

    page = Page()
    page.feed(DASHBOARD_HTML)
    assert len(page.ids) == len(set(page.ids))
    assert set(re.findall(r"\$\('([^']+)'\)", DASHBOARD_HTML)) <= set(page.ids)
    section = DASHBOARD_HTML.split("Recent Runs", 1)[1].split("</section>", 1)[0]
    headers = re.findall(r"<th[^>]*>([^<]+)</th>", section)
    assert headers[0] == "Status"
    assert headers[-3:] == ["Backend", "Requested / Effective Model", "Effort"]

    node = shutil.which("node")
    if node is None:
        pytest.skip("optional Node.js runtime not installed")
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element {
  constructor(){this.children=[];this.events={};this.value='';this._text='';this.open=false}
  append(...children){this.children.push(...children)}
  appendChild(child){this.children.push(child)}
  replaceChildren(...children){this.children=children;this._text=''}
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(child=>child.textContent||'').join('')}
  addEventListener(key,handler){this.events[key]=handler}
}
const nodes=Object.fromEntries(input.ids.map(id=>[id,new Element()])),calls=[];
nodes['runs-symbol'].value='all';
const context=vm.createContext({console,Date,Number,Math,
  document:{getElementById:id=>{assert.ok(nodes[id],id);return nodes[id]},createElement:()=>new Element()},
  location:{replace:()=>{}},setInterval:()=>0,
  fetch:(url,options)=>{calls.push({url,options});return new Promise(()=>{})}});
vm.runInContext(input.script,context,{timeout:1000});
context.data={recent_runs:[
  {symbol:'BTCUSDT',run_status:'failed',error_type:'provider_error',outcome:'trade',
   paper_status:'rejected',paper_reason:'<script>full reason</script>',
   analysis_backend:'codex_agent',requested_model:'requested',effective_model:'effective',
   effective_reasoning_effort:'xhigh'},
  {symbol:'ETHUSDT',run_status:'completed',outcome:'wait',paper_status:'no_trade'}]};
const original=JSON.stringify(context.data);
vm.runInContext('latest=data;renderRuns(data.recent_runs)',context);
assert.equal(nodes.runs.children.length,2);
const row=nodes.runs.children[0];
assert.equal(row.children.length,13);
assert.equal(row.children[0].textContent,'failed');
assert.ok(row.children[0].className.includes('run-status'));
assert.equal(row.children[0].title,'provider_error');
assert.ok(row.children[3]._text.includes('trade · Paper: rejected'));
const detail=row.children[3].children[0];
assert.equal(detail.open,false);
assert.equal(detail.children[1].textContent,'<script>full reason</script>');
assert.equal(row.children[10].textContent,'codex_agent');
assert.equal(row.children[11].textContent,'requested / effective');
assert.equal(row.children[12].textContent,'xhigh');
nodes['runs-symbol'].value='ETHUSDT';nodes['runs-symbol'].events.change();
assert.equal(nodes.runs.children.length,1);
assert.equal(nodes.runs.children[0].children[0].textContent,'completed');
assert.ok(nodes.runs.children[0].textContent.includes('ETHUSDT'));
assert.equal(nodes['runs-count'].textContent,'1 / 2');
nodes['runs-symbol'].value='XAUUSDT';nodes['runs-symbol'].events.change();
assert.ok(nodes.runs.textContent.includes('该品种暂无记录'));
nodes['runs-symbol'].value='all';nodes['runs-symbol'].events.change();
assert.equal(nodes.runs.children.length,2);
assert.equal(JSON.stringify(context.data),original);
assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/status');
assert.equal(calls[0].options.method,undefined);
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"script": script, "ids": page.ids}),
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
