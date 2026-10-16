/* Shared, dependency-free UI primitives. Inlined by hosts; no data fetching or persistence. */
(() => {
  'use strict';
  let layerSerial = 0;
  const el = (tag, className = '', text = '') => {
    const node = document.createElement(tag);
    if(className)node.className=className;
    if(text)node.textContent=text;
    return node;
  };
  const chevron = () => {
    const ns='http:' + '//www.w3.org/2000/svg';
    const svg=document.createElementNS(ns,'svg'),path=document.createElementNS(ns,'path');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');
    svg.setAttribute('stroke-width','1.6');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');
    svg.setAttribute('aria-hidden','true');svg.classList.add('ui-icon');path.setAttribute('d','M9 6l6 6-6 6');svg.append(path);return svg;
  };
  function create({selectOption = () => ({})} = {}) {
  // Floating layers live outside scroll panels so they never clip at a sidebar edge.
  let activeLayer = null, previewTimer = null, exitTimer = null;
  function closeLayer() {
    clearTimeout(previewTimer); clearTimeout(exitTimer);
    const previous = activeLayer; activeLayer = null;
    if (previous) { previous.cleanup?.(); previous.panel.remove(); }
  }
  function placeLayer(panel, anchor, side = false) {
    const rect = anchor.getBoundingClientRect(), margin = 12;
    const width = Math.min(side ? 360 : Math.max(280, rect.width), innerWidth - margin * 2);
    panel.style.width = `${width}px`;
    panel.style.maxHeight = `${Math.max(120, innerHeight - margin * 2)}px`;
    const height = panel.offsetHeight; // Measure layout, not the animated transform.
    let left = side && innerWidth - rect.right >= width + 20 ? rect.right + 10 : rect.left;
    let top = side && left > rect.right ? rect.top : rect.bottom + 8;
    if (top + height > innerHeight - margin) top = side ? innerHeight - height - margin : rect.top - height - 8;
    panel.style.left = `${Math.max(margin, Math.min(left, innerWidth - width - margin))}px`;
    panel.style.top = `${Math.max(margin, top)}px`;
  }
  function keepPreview() { clearTimeout(exitTimer); }
  function leavePreview() {
    clearTimeout(previewTimer);
    if(activeLayer?.kind!=='preview')return;
    const layer=activeLayer;
    exitTimer=setTimeout(()=>{
      if(activeLayer===layer&&!layer.pinned&&!layer.anchor.matches(':hover,:focus-visible')&&!layer.panel.matches(':hover'))closeLayer();
    },180);
  }
  function showPreview(anchor, build, pinned = false) {
    closeLayer(); if (!anchor.isConnected) return;
    const panel = el('aside', 'quick-preview'); panel.id = `preview-${++layerSerial}`;
    panel.setAttribute('role', 'tooltip'); build(panel);
    const previousDescription = anchor.getAttribute('aria-describedby');
    anchor.setAttribute('aria-describedby', [previousDescription, panel.id].filter(Boolean).join(' '));
    if(anchor.hasAttribute('aria-expanded'))anchor.setAttribute('aria-expanded','true');
    activeLayer = {panel, anchor, pinned, kind:'preview', cleanup() {
      if(anchor.hasAttribute('aria-expanded'))anchor.setAttribute('aria-expanded','false');
      if (previousDescription) anchor.setAttribute('aria-describedby', previousDescription); else anchor.removeAttribute('aria-describedby');
    }};
    panel.addEventListener('pointerenter', keepPreview); panel.addEventListener('pointerleave', leavePreview);
    document.body.append(panel); placeLayer(panel, anchor, true);
  }
  function bindPreview(anchor, build, toggle = false) {
    anchor.addEventListener('pointerenter', event => {
      if (event.pointerType === 'touch' || activeLayer?.kind==='select' || activeLayer?.pinned) return;
      clearTimeout(previewTimer); clearTimeout(exitTimer);
      previewTimer = setTimeout(() => showPreview(anchor, build), 350);
    });
    anchor.addEventListener('pointerleave', leavePreview);
    anchor.addEventListener('focus', () => { if (anchor.matches(':focus-visible')) showPreview(anchor, build); });
    anchor.addEventListener('blur', leavePreview);
    if (toggle) {
      anchor.setAttribute('aria-expanded','false');
      anchor.addEventListener('click', () => {
        if(activeLayer?.anchor===anchor&&activeLayer.pinned)closeLayer();
        else showPreview(anchor,build,true);
      });
    }
  }
  const selectViews = new WeakMap();
  function enhanceSelect(select) {
    if(select.hidden || !select.options.length)return;
    const originalId = select.id || `select-${++layerSerial}`, label = document.querySelector(`label[for="${CSS.escape(originalId)}"]`);
    const button = el('button','select-trigger'); button.type = 'button'; button.id = originalId;
    const name = select.getAttribute('aria-label') || label?.textContent || '选择';
    button.setAttribute('role','combobox'); button.setAttribute('aria-label',name);
    button.setAttribute('aria-haspopup','listbox'); button.setAttribute('aria-expanded','false');
    select.id = `${originalId}-native`; select.hidden = true; select.after(button);
    const describe = option => selectOption(option) || {};
    function sync() {
      button.setAttribute('aria-disabled',String(select.disabled));
      const selected = select.selectedOptions[0];
      button.dataset.tone = selected ? describe(selected).tone || '' : '';
      button.replaceChildren(el('span','select-value',(selected && describe(selected).label) || selected?.textContent || '请选择'),chevron());
      button.querySelector('svg').classList.add('select-chevron');
    }
    sync(); selectViews.set(select,sync);
    let index = select.selectedIndex, buffer = '', typedAt = 0;
    function commit() {
      const value = select.options[index]?.value; closeLayer();
      if (value === undefined || value === select.value) return;
      select.value = value; sync();
      select.dispatchEvent(new Event('input',{bubbles:true}));
      select.dispatchEvent(new Event('change',{bubbles:true})); sync();
    }
    function open() {
      closeLayer(); index = select.selectedIndex;
      const panel = el('div','select-menu'); panel.id = `select-menu-${++layerSerial}`;
      panel.setAttribute('role','listbox'); panel.setAttribute('aria-label',name);
      button.setAttribute('aria-controls',panel.id); button.setAttribute('aria-expanded','true');
      [...select.options].forEach((option,i) => {
        const row = el('div','select-option'); row.id = `${panel.id}-${i}`; row.setAttribute('role','option');
        row.setAttribute('aria-label',option.textContent); row.dataset.tone = describe(option).tone || '';
        const copy = el('span','select-option-copy'); copy.append(el('strong','',describe(option).label || option.textContent));
        const help = describe(option).description || '';
        if(help)copy.append(el('small','',help)); row.append(el('span','select-dot'),copy,el('span','select-check',option.selected ? '✓' : ''));
        row.addEventListener('pointerdown',event => event.preventDefault());
        row.addEventListener('click',() => { index=i; commit(); }); panel.append(row);
      });
      activeLayer = {panel,anchor:button,kind:'select',cleanup() {
        button.setAttribute('aria-expanded','false'); button.removeAttribute('aria-activedescendant'); button.removeAttribute('aria-controls');
      }};
      document.body.append(panel); placeLayer(panel,button); highlight();
    }
    function highlight() {
      if (activeLayer?.anchor !== button) return;
      const rows = [...activeLayer.panel.children];
      rows.forEach((row,i) => { row.setAttribute('aria-selected',String(i===index)); row.classList.toggle('is-active',i===index); });
      button.setAttribute('aria-activedescendant',rows[index].id);
      const menu=activeLayer.panel, top=rows[index].offsetTop, bottom=top+rows[index].offsetHeight;
      if(top<menu.scrollTop)menu.scrollTop=top;
      else if(bottom>menu.scrollTop+menu.clientHeight)menu.scrollTop=bottom-menu.clientHeight;
    }
    button.addEventListener('click',() => { if(!select.disabled) activeLayer?.anchor === button ? closeLayer() : open(); });
    button.addEventListener('blur',() => { if (activeLayer?.anchor === button) closeLayer(); });
    button.addEventListener('keydown',event => {
      if(select.disabled) return;
      const wasOpen = activeLayer?.anchor === button;
      if (event.key==='Escape') { if(wasOpen){event.preventDefault();closeLayer();} return; }
      if (event.key==='Tab') { if(wasOpen) commit(); return; }
      if (['Enter',' '].includes(event.key)) { event.preventDefault(); wasOpen ? commit() : open(); return; }
      const directions = ['ArrowDown','ArrowUp','Home','End','PageDown','PageUp'];
      if (directions.includes(event.key)) {
        event.preventDefault(); if(!wasOpen) open();
        if(event.key==='Home') index=0; else if(event.key==='End') index=select.options.length-1;
        else if(wasOpen) index=Math.max(0,Math.min(select.options.length-1,index+({ArrowDown:1,ArrowUp:-1,PageDown:10,PageUp:-10}[event.key])));
        highlight(); return;
      }
      if(event.key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey) {
        event.preventDefault(); if(!wasOpen)open();
        buffer = Date.now()-typedAt>700 ? event.key : buffer+event.key; typedAt=Date.now();
        const repeated=[...buffer].every(character=>character===buffer[0]); const query=(repeated?buffer[0]:buffer).toLocaleLowerCase();
        const options=[...select.options]; const next=options.findIndex((_,offset)=>options[(index+1+offset)%options.length].textContent.toLocaleLowerCase().startsWith(query));
        if(next>=0)index=(index+1+next)%options.length; highlight();
      }
    });
  }
  document.addEventListener('pointerdown',event => {
    if(activeLayer&&!activeLayer.panel.contains(event.target)&&!activeLayer.anchor.contains(event.target))closeLayer();
  });
  document.addEventListener('keydown',event => {if(event.key==='Escape')closeLayer();});
  document.addEventListener('scroll',event => {if(activeLayer&&!activeLayer.panel.contains(event.target))closeLayer();},true);
  window.addEventListener('resize',closeLayer);

    return Object.freeze({closeLayer, bindPreview, enhanceSelect, syncSelect(select) { selectViews.get(select)?.(); }});
  }
  window.WorkspaceUI = Object.freeze({create});
})();
