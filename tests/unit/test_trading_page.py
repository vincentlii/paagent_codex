from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from pa_agent.trading_page import TRADING_HTML


def test_trading_page_ids_are_unique_and_all_script_targets_exist():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key == "id":
                    self.ids.append(value)

    page = Page()
    page.feed(TRADING_HTML)
    assert len(page.ids) == len(set(page.ids))
    assert set(re.findall(r"\$\('([^']+)'\)", TRADING_HTML)) <= set(page.ids)
    assert 'href="/trading" aria-current="page"' in TRADING_HTML
    assert '<optgroup label="历史模拟记录">' in TRADING_HTML


def test_trading_script_renders_unknown_empty_and_review_without_writes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("optional Node.js runtime not installed")
    script = re.search(r"<script>(.*?)</script>", TRADING_HTML, re.DOTALL).group(1)
    syntax = subprocess.run(
        [node, "--check"], input=script, text=True, capture_output=True, timeout=5, check=False
    )
    assert syntax.returncode == 0, syntax.stderr
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element {
  constructor(){this.children=[];this.attrs={};this.events={};this.value='';this.open=false;this._text=''}
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=children;this._text=''}
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(child=>child.textContent||'').join('')}
  setAttribute(key,value){this.attrs[key]=String(value)}
  addEventListener(key,handler){this.events[key]=handler}
  querySelectorAll(){return []}
  showModal(){this.open=true}
  close(){this.open=false}
}
const nodes=Object.fromEntries(input.ids.map(id=>[id,new Element()])),calls=[];
nodes.period.value='all';nodes.symbol.value='all';
const context=vm.createContext({console,Date,Number,Math,Set,AbortController,
  document:{getElementById:id=>{assert.ok(nodes[id],id);return nodes[id]},
    createElement:()=>new Element(),createElementNS:()=>new Element()},
  location:{replace:()=>{}},setInterval:()=>0,setTimeout:()=>0,clearTimeout:()=>{},
  fetch:(url,options)=>{calls.push({url,options});return new Promise(()=>{})}});
vm.runInContext(input.script,context,{timeout:1000});
context.data={health:{state:'running',label:'运行中'},runtime:{assets:[]},trading:{
  schema_version:1,generated_at_ms:Date.now(),analysis_available:true,paper_available:true,decisions_available:true,
  account:{settled_balance:10000,open_risk:0,pending_risk:0,open_count:0,marks_known:0,unrealized_pnl:0},
  periods:{all:{start_ms:0,assets:['XAUUSDT','BTCUSDT','ETHUSDT'].map(symbol=>({symbol,
    tasks:5,completed:4,failed:1,outstanding:0,model_no_trade:3,candidates:1,joint_completed:4,
    orders:1,filled:0,pending:1,open:0,closed:0,pnl_known:0,realized_pnl:0,ambiguous:0})),curve:[]}},
  orders:[],analyses:[]}};
vm.runInContext('render(data)',context,{timeout:1000});
assert.equal(nodes.assets.children.length,3);
assert.ok(nodes.assets.children[0].textContent.includes('4 / 5'));
assert.ok(nodes.orders.textContent.includes('无订单'));
assert.ok(nodes.chart.textContent.includes('暂无已知平仓'));
vm.runInContext("showReview({run_id:'review',review:{reasoning:'<script>plain text</script>'}},false)",context);
assert.ok(nodes['review-content'].textContent.includes('<script>plain text</script>'));
vm.runInContext("showReview({review:{vpa_timing:'insufficient_data',vpa_entry_applicable:false,vpa_data_status:'available'}},false)",context);
assert.ok(nodes['review-content'].textContent.includes('不适用（尚无具体入场尝试）'));
vm.runInContext("showReview({review:{vpa_timing:'insufficient_data',vpa_entry_applicable:false,vpa_data_status:'unavailable'}},false)",context);
assert.ok(nodes['review-content'].textContent.includes('量价数据不可用'));
context.data.trading.scope={simulation_version:'paper_latency_v4'};
context.data.trading.execution_versions={paper_latency_v3:{...context.data.trading,
  scope:{simulation_version:'paper_latency_v3'},account:{settled_balance:9876},orders:[]}};
nodes['execution-version'].value='paper_latency_v3';nodes['execution-version'].events.change();
assert.ok(nodes.version.textContent.includes('paper_latency_v3'));
assert.ok(nodes.balance.textContent.includes('9,876'));
nodes['execution-version'].value='paper_latency_v4';nodes['execution-version'].events.change();
assert.ok(nodes.version.textContent.includes('paper_latency_v4'));
assert.ok(nodes.notice.textContent.includes('v3 尚有 3 笔挂单/持仓'));
assert.equal(nodes['review-dialog'].open,true);
nodes['close-review'].events.click();assert.equal(nodes['review-dialog'].open,false);
vm.runInContext("fetchError='connection failed; cached data';render(data)",context);
assert.ok(nodes.notice.textContent.includes('connection failed'));
nodes.period.events.change();
assert.ok(nodes.notice.textContent.includes('connection failed'));
vm.runInContext("render({health:{state:'offline'},trading:{}})",context);
assert.ok(nodes.notice.textContent.includes('尚未发布'));
assert.equal(nodes.balance.textContent,'—');
context.data={health:{state:'running'},runtime:{},trading:{schema_version:2,selected_backend:'okx_demo',
  demo:{schema_version:1,environment:'okx_demo_v1',state:'ready',generated_at_ms:Date.now(),
    exchange_observed_at_ms:Date.now(),account:{equity:'10000',available_balance:'9000'},
    periods:{all:{net_pnl:'7',fees:'-1',funding:'-2',external_flows:'10000',curve:[]}},orders:[],positions:[],
    fills:[{instId:'BTC-USDT-SWAP',side:'buy',fillPx:'50000',fillSz:'0.4',fee:'-0.1',feeCcy:'USDT',ts:String(Date.now())}]},
  paper_versions:{paper_latency_v4:{schema_version:1,account:{settled_balance:99999},periods:{all:{assets:[]}}}},
  analysis:{analyses:[],periods:{all:{assets:[]}}}}};
vm.runInContext('render(data)',context);
assert.equal(nodes['execution-version'].value,'okx_demo');
assert.ok(nodes.balance.textContent.includes('10,000'));
assert.ok(nodes.version.textContent.includes('OKX 模拟盘'));
assert.ok(!nodes.version.textContent.includes('funding 未建模'));
assert.ok(nodes['demo-fills'].textContent.includes('BTC-USDT-SWAP'));
assert.ok(nodes['demo-fills'].textContent.includes('-0.1 USDT'));
nodes['execution-version'].value='paper_latency_v4';nodes['execution-version'].events.change();
assert.ok(nodes.balance.textContent.includes('99,999'));
context.data.trading.paper_versions.paper_latency_v4.history_frozen=true;
vm.runInContext('render(data)',context);
assert.ok(nodes['backend-label'].textContent.includes('历史模拟记录'));
assert.ok(nodes.notice.textContent.includes('不再观察行情'));
assert.equal(nodes.unrealized.textContent,'—');
assert.equal(nodes.risk.textContent,'—');
assert.ok(!nodes.notice.textContent.includes('尚有'));
vm.runInContext('render(data)',context);assert.equal(nodes['execution-version'].value,'paper_latency_v4');
nodes['execution-version'].value='okx_demo';nodes['execution-version'].events.change();
vm.runInContext('render({trading:{schema_version:1,account:{settled_balance:99999}}})',context);
assert.equal(nodes.balance.textContent,'—');assert.ok(nodes.notice.textContent.includes('不回退'));
assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/trading');
assert.equal(calls[0].options.method,undefined);
context.data.health={state:'running'};
context.data.demo_execution_settings={supported:true,control_generation:8,pending:false,
  desired:{notional_usdt:'500',limit_validity_hours:3,generation:0},
  applied:{notional_usdt:'500',limit_validity_hours:3,generation:0}};
vm.runInContext('render(data)',context);
assert.equal(nodes['demo-notional'].value,'500');
assert.equal(nodes['save-demo-settings'].disabled,false);
assert.ok(nodes['demo-settings-status'].textContent.includes('Host 已生效'));
nodes['demo-notional'].value='750';nodes['demo-notional'].events.input();
nodes['demo-validity'].value='6';nodes['demo-validity'].events.input();
context.data.demo_execution_settings.desired.notional_usdt='900';
vm.runInContext('render(data)',context);
assert.equal(nodes['demo-notional'].value,'750'); // polling preserves unsaved edits
assert.equal(calls.length,1); // viewing/editing never saves automatically
nodes['save-demo-settings'].events.click();
assert.equal(calls.length,2);assert.equal(calls[1].url,'/api/demo-execution-settings');
assert.equal(calls[1].options.method,'POST');
assert.equal(calls[1].options.headers['X-PA-Agent-Control'],'1');
assert.deepEqual(JSON.parse(calls[1].options.body),{expected_generation:8,
  settings:{notional_usdt:'750',limit_validity_hours:6}});
console.log('Trading rendering and explicit audited settings save: OK');
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"script": script, "ids": re.findall(r'id="([^"]+)"', TRADING_HTML)}),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
