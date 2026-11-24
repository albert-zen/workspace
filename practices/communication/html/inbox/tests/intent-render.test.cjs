const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  const roots = [];
  class Node {
    constructor(tag, text = '') {
      this.tagName = tag.toUpperCase();
      this.textContent = text;
      this.children = [];
      this.dataset = {};
      this.hidden = false;
      this.open = false;
      this.isConnected = true;
      this.listeners = {};
      this.classList = { add() {} };
    }
    append(...nodes) {
      for (const node of nodes) {
        node.parentElement = this;
        this.children.push(node);
      }
    }
    replaceChildren(...nodes) {
      this.children = [];
      this.append(...nodes);
    }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    setAttribute(name, value) { this[name] = value; }
    matches() { return false; }
  }
  const walk = root => [root, ...root.children.flatMap(walk)];
  const document = {
    listeners: {},
    addEventListener(name, listener) { this.listeners[name] = listener; },
    createElement: tag => new Node(tag),
    createElementNS: (_, tag) => new Node(tag),
    createTextNode: text => new Node('#text', text),
    getElementById: id => roots.flatMap(walk).find(node => node.id === id) || null,
  };
  const context = vm.createContext({
    document,
    fetch: () => Promise.reject(new Error('unused')),
    window: { addEventListener() {} },
    navigator: { clipboard: { writeText: async () => {} } },
    setTimeout,
    clearTimeout,
    console,
  });
  const source = fs.readFileSync(process.env.INBOX_JS || require.resolve('../assets/inbox.js'), 'utf8');
  const boundary = source.indexOf('  const ui = WorkspaceUI.create(');
  assert.ok(boundary > 0);
  vm.runInContext(
    source.slice(0, boundary) + '\n globalThis.api={renderWorkDoc};\n})();',
    context
  );
  return { ...context.api, root() { const r = new Node('section'); roots.push(r); return r; }, walk };
}

test('renderWorkDoc renders work intent open and project intent collapsed using safe blocks', () => {
  const h = harness();
  const shell = h.root();
  h.renderWorkDoc(shell, {
    path: 'agent-data/projects/demo/work/active/Item.md',
    absolutePath: '/ws/agent-data/projects/demo/work/active/Item.md',
    sections: {
      description: { id: '事项说明', blocks: [{ type: 'paragraph', inlines: [{ type: 'text', text: 'Desc' }] }] },
      progress: { id: '当前推进', blocks: [{ type: 'paragraph', inlines: [{ type: 'text', text: 'Prog' }] }] },
    },
    remainingBlocks: [],
    intents: [
      {
        scope: 'work',
        label: '事项原始意图',
        path: 'agent-data/projects/demo/work/active/Item/intent.md',
        absolutePath: '/ws/agent-data/projects/demo/work/active/Item/intent.md',
        blocks: [
          { type: 'heading', level: 2, id: 't1', inlines: [{ type: 'text', text: '2026-11-24' }] },
          { type: 'blockquote', inlines: [{ type: 'text', text: '用户原话 <script>alert(1)</script>' }] },
        ],
      },
      {
        scope: 'project',
        label: '项目原始意图',
        path: 'agent-data/projects/demo/intent.md',
        absolutePath: '/ws/agent-data/projects/demo/intent.md',
        blocks: [{ type: 'blockquote', inlines: [{ type: 'text', text: '长期目标' }] }],
      },
    ],
  });

  const details = h.walk(shell).filter(node => node.tagName === 'DETAILS');
  assert.equal(details.length, 3);
  assert.equal(details[0].dataset.panelKey, 'intent:work');
  assert.equal(details[0].open, true);
  assert.equal(details[0].children[0].textContent, '事项原始意图');
  assert.equal(details[1].dataset.panelKey, 'intent:project');
  assert.equal(details[1].open, false);
  assert.equal(details[1].children[0].textContent, '项目原始意图');
  assert.equal(details[2].dataset.panelKey, 'evidence');

  const quotes = h.walk(details[0]).filter(node => node.tagName === 'BLOCKQUOTE');
  assert.equal(quotes.length, 1);
  assert.equal(quotes[0].children[0].tagName, '#TEXT');
  assert.equal(quotes[0].children[0].textContent, '用户原话 <script>alert(1)</script>');
});
