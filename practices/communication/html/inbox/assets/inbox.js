// Work links preserve legacy project scope; named captures work before a document exists.
const InboxProjects = {
  canLeaveEdit(route, saving) { return !saving && route!=="edit"; },
  key(item) {
    const match = (item.workPath || "").match(/^agent-data\/projects\/(.+)\/work\/(?:active|archive)\/[^/]+\.md$/);
    if(match)return match[1];
    return typeof item.semanticName==="string"&&item.semanticName.includes("/")?item.semanticName.split("/",1)[0]:"";
  },
  disclosure({show,hide,beforeSwitch=()=>()=>{},now=()=>Date.now(),schedule=setTimeout,cancel=clearTimeout}) {
    let active=null,pending=null,timer=null,closeTimer=null,blockedUntil=0,lastPoint=null,hovered=null,focused=null;
    function stop(){cancel(timer);timer=null;pending=null;}
    function keep(){cancel(closeTimer);closeTimer=null;}
    function close(){keep();if(active!==null)hide(active);active=null;}
    function deferClose(){
      keep();if(active===null||active===hovered||active===focused)return;
      closeTimer=schedule(()=>{if(active!==hovered&&active!==focused)close();},Math.max(180,blockedUntil-now()));
    }
    function open(key){stop();keep();if(active===key)return;const restore=beforeSwitch(key);if(active!==null)hide(active);active=key;show(key);restore();}
    return {
      enter(key){hovered=key;if(active===key)keep();},
      move(key,x,y){
        hovered=key;if(active===key)keep();
        if(lastPoint&&lastPoint.x===x&&lastPoint.y===y)return;
        lastPoint={x,y};
        if(now()<blockedUntil)return;
        if(key===active){stop();return;}
        if(pending===key)return;
        stop();pending=key;timer=schedule(()=>open(key),450);
      },
      leave(key){if(hovered===key)hovered=null;if(pending===key)stop();deferClose();},
      scroll(){stop();blockedUntil=now()+220;deferClose();},
      focus(key){focused=key;open(key);},
      blur(key){if(focused===key)focused=null;deferClose();},
      escape(){stop();close();},
      reset(){stop();keep();active=null;hovered=null;focused=null;lastPoint=null;},
      current(){return active;}
    };
  },
  descriptionText(doc) {
    const inline=nodes=>(nodes||[]).map(node=>node.text||"").join("");
    return (doc.sections?.description?.blocks||[]).map(block=>block.type==="list"
      ? block.items.map(item=>inline(item.inlines)).join(" ")
      : block.inlines ? inline(block.inlines) : block.text||"").join(" ").replace(/\s+/g," ").trim();
  },
  handoff(entryKey, inbox) {
    const matches=inbox.sections.flatMap(section=>section.items).filter(item=>item.entryKey===entryKey);
    if(matches.length!==1)throw new Error("事项已发生变化，请刷新 Inbox 后重新复制。");
    if(typeof matches[0].handoffCommand==="string"&&matches[0].handoffCommand.trim())return `请接手我 Inbox 中的事项「${matches[0].title}」。\n\n    ${matches[0].handoffCommand}`;
    if(typeof inbox.absoluteSourcePath!=="string"||!inbox.absoluteSourcePath.trim())throw new Error("无法取得 Inbox 完整路径，请更新服务后重试。");
    const windows=/^(?:[a-z]:[\\/]|\\\\)/i.test(inbox.absoluteSourcePath);
    const quote=value=>"'"+value.replace(/'/g,windows?"''": "'\"'\"'")+"'";
    const command=`rg -n -F -- ${quote(matches[0].title+" — ")} ${quote(inbox.absoluteSourcePath)}`;
    return `请接手我 Inbox 中的事项「${matches[0].title}」。\n\n先用以下命令定位事项，再沿其中的 Work 链接读取材料：\n\n    ${command}`;
  },
  label(key) { return key === "" ? "未分类" : key === "workspace" ? "工作空间" : key; },
  options(sections) {
    const counts = new Map();
    for (const section of sections) for (const item of section.items) {
      const key = this.key(item);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts].sort(([a], [b]) => a.localeCompare(b));
  },
  filter(sections, project) {
    return sections.map(section => ({...section, items: section.items.filter(item => project === null || this.key(item) === project)}));
  },
  move(sections, key, target) {
    const matches=sections.flatMap(s=>s.items).filter(i=>i.entryKey===key);
    const destination=sections.find(s=>s.status===target.status);
    if(matches.length!==1||!destination)throw new Error("事项或区块已变化，请刷新后重试。");
    const anchor=target.beforeKey??target.afterKey;
    if(anchor!==undefined&&!destination.items.some(i=>i.entryKey===anchor))throw new Error("目标事项已变化，请刷新后重试。");
    if(anchor===undefined&&!['start','end'].includes(target.edge))throw new Error("请指定移动位置。");
    if(anchor===key)return sections;
    const next=sections.map(s=>({...s,items:s.items.filter(i=>i.entryKey!==key)}));
    const items=next.find(s=>s.status===target.status).items;
    const pinned=!!matches[0].pinned;
    if(anchor!==undefined&&!!items.find(i=>i.entryKey===anchor).pinned!==pinned)throw new Error("置顶事项固定在顶部；请先置顶或取消置顶，再调整分组。");
    const index=anchor===undefined
      ? target.edge==='start'?(pinned?0:items.filter(i=>i.pinned).length):(pinned?items.filter(i=>i.pinned).length:items.length)
      : items.findIndex(i=>i.entryKey===anchor)+(target.afterKey!==undefined?1:0);
    items.splice(index,0,matches[0]);return next;
  },
  setPin(sections,key,pinned) {
    const section=sections.find(s=>s.items.some(i=>i.entryKey===key));
    if(!section)throw new Error("事项已变化，请刷新后重试。");
    const next=sections.map(s=>({...s,items:s.items.map(i=>i.entryKey===key?{...i,pinned}:i)}));
    return this.move(next,key,{status:section.status,edge:'start'});
  }
};
// One serialized refresh queue. Notifications coalesce; blocked interactions retry later.
InboxProjects.refreshQueue = function({load,apply,blocked,epoch,status,schedule=setTimeout,cancel=clearTimeout}) {
  let version="",pending=false,running=false,timer=null;
  function later(ms){cancel(timer);timer=schedule(drain,ms);}
  async function drain(){
    if(!pending||running)return;
    if(blocked()){later(500);return;}
    pending=false;running=true;const started=epoch();let retry=0;
    try{
      const data=await load(version);
      if(blocked()||epoch()!==started)pending=true;
      else{apply(data);version=data.version;status("已同步");}
    }catch(error){status("同步暂不可用，正在重试");pending=true;retry=2000;}
    finally{running=false;if(pending)later(retry);}
  }
  return {
    notify(next){if(next&&next===version)return;pending=true;drain();},
    accepted(next){version=next;},
  };
};
if (typeof module !== "undefined") module.exports = InboxProjects;
if (typeof document !== "undefined") (() => {
  "use strict";
  const STATUS = ["Needs you", "In progress", "Waiting", "Done"];
  const LABEL = {"Needs you":"需要你处理","In progress":"进行中","Waiting":"等待中","Done":"已完成"};
  const STATUS_CLASS = {"Needs you":"needs","In progress":"progress","Waiting":"waiting","Done":"done"};
  const $ = (id) => document.getElementById(id);
  // Keep the native control reference: enhancement transfers its id to the trigger.
  const projectSelect = $("project-filter");
  const desktop = () => matchMedia("(min-width: 721px)").matches;
  const state = {revision:"",sections:[],work:[],selected:null,route:"list",draft:null,restoreKey:null,localMessage:"",project:null};

  let viewEpoch=0, saving=0, loading=false, liveVersion="", workVersions={}, preparedWork=null, lastScroll=0, dragging=null, arrangementError=false, suppressOpenUntil=0;
  const collapsedStatuses = new Set(["Done"]);

  function el(tag,className="",text="") { const node=document.createElement(tag);if(className)node.className=className;if(text)node.textContent=text;return node; }
  function itemByKeyIn(sections,key) { for(const section of sections)for(const item of section.items)if(item.entryKey===key)return{section,item};return null; }
  function itemByKey(key) { return itemByKeyIn(state.sections,key); }
  function itemView(found){return found?{status:found.section.status,item:found.item}:null;}
  function workByPath(path) { return state.work.find(item=>item.path===path)||null; }
  function clearGlobalError(){const node=$("app-error");node.hidden=true;node.replaceChildren();}
  function showGlobalError(error){const node=$("app-error");node.replaceChildren(el("strong","",error.message||"无法读取 Inbox。"),el("span","",error.action||"请刷新后重试。"));node.hidden=false;node.focus();}
  async function jsonFetch(url,options={}){const response=await fetch(url,{cache:"no-store",...options});let data;try{data=await response.json();}catch(_){data={error:{message:"服务返回了无法读取的响应。",action:"刷新页面后重试。"}};}if(!response.ok){const failure=new Error(data.error?.message||response.statusText);failure.detail=data.error||{};failure.status=response.status;throw failure;}return data;}

  async function load({preserveRoute=false}={}) {
    if(state.route==="edit"){ $("sync-state").textContent="草稿已保留，请保存或取消后刷新";return; }
    loading=true;const started=++viewEpoch;
    arrangementError=false;clearGlobalError();$("workspace").setAttribute("aria-busy","true");$("sync-state").textContent="读取中";
    const intent={route:state.route,key:state.selected,draft:state.draft};
    try {
      const snapshot=await jsonFetch("/api/snapshot"),inbox=snapshot.inbox,work={documents:snapshot.documents};
      if(viewEpoch!==started)return;
      const selected=intent.key&&itemByKeyIn(inbox.sections,intent.key),invalidRoute=preserveRoute&&((intent.route==="detail"&&!selected)||(intent.route==="edit"&&intent.key&&!selected)),reset=!preserveRoute||invalidRoute;
      const next={revision:inbox.revision,sections:inbox.sections,work:work.documents,route:reset?"list":intent.route,selected:reset?null:intent.key,draft:reset?null:intent.draft};
      if(desktop()&&next.route==="list"&&!next.selected){const first=next.sections.filter(section=>!collapsedStatuses.has(section.status)).flatMap(section=>section.items).find(item=>state.project===null||InboxProjects.key(item)===state.project);if(first){next.selected=first.entryKey;next.route="detail";}}
      liveVersion=snapshot.version;workVersions=snapshot.workVersions;live.accepted(liveVersion);
      Object.assign(state,next);$("sync-state").textContent="已同步";
      if(invalidRoute)history.replaceState({route:"list",key:null},"","#inbox");
      render();
    } catch(error) { $("sync-state").textContent="读取失败";showGlobalError(error.detail||{message:error.message}); }
    finally { loading=false;$("workspace").setAttribute("aria-busy","false"); }
  }
  function route(next,{push=true,focus=true,replace=false}={}) { state.route=next;document.body.dataset.route=next;if(push)history[replace?"replaceState":"pushState"]({route:next,key:state.selected},"",next==="list"?"#inbox":`#${next}/${state.selected||"new"}`);render();if(focus)requestAnimationFrame(()=>{if(next==="list")restoreFocus();else{$("detail-panel").scrollTop=0;if(!desktop())window.scrollTo(0,0);if(!document.activeElement?.closest(".entry-form"))$("detail-panel").focus({preventScroll:true});}}); }
  function restoreFocus(){const target=state.restoreKey&&document.querySelector(`[data-entry-key="${CSS.escape(state.restoreKey)}"]`);(target||$("list-heading")).focus?.();}
  function mayLeaveEdit(){
    if(saving){$("sync-state").textContent="保存中，请稍后再切换";return false;}
    if(InboxProjects.canLeaveEdit(state.route,saving))return true;
    const form=document.querySelector(".entry-form");
    if(form)showFormError(form,{message:"当前输入尚未保存，草稿已保留。",action:"请先保存，或点击取消放弃本次编辑，再切换事项。"});
    return false;
  }
  function openItem(key,push=true){if(!mayLeaveEdit())return;state.restoreKey=key;state.selected=key;state.draft=null;state.localMessage="";route("detail",{push});}
  function backToList(){if(!mayLeaveEdit())return;state.draft=null;state.selected=null;route("list");}
  function beginNew(){if(!mayLeaveEdit())return;state.selected=null;state.draft={title:"",summary:"",status:"Needs you",issueUrl:"",workPath:"",originalKey:null};state.localMessage="";route("edit");}

  function navigationIcon(name){const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg"),path=document.createElementNS(ns,"path");svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("fill","none");svg.setAttribute("stroke","currentColor");svg.setAttribute("stroke-width","1.6");svg.setAttribute("stroke-linecap","round");svg.setAttribute("stroke-linejoin","round");svg.setAttribute("aria-hidden","true");svg.classList.add("ui-icon");path.setAttribute("d",({back:"M15 6l-6 6 6 6",next:"M9 6l6 6-6 6",copy:"M8 8h12v12H8z M16 8V4H4v12h4",check:"M5 12l4 4L19 6",edit:"M14 5l5 5 M4 20l4-1 12-12a2.1 2.1 0 0 0-3-3L5 16z",plus:"M12 5v14 M5 12h14",error:"M12 7v6 M12 17h.01",top:"M5 4h14 M12 20V8 M7 13l5-5 5 5",pin:"M9 3h6l-1 6 4 4v2H6v-2l4-4z M12 15v6",drag:"M8 5h.01 M16 5h.01 M8 12h.01 M16 12h.01 M8 19h.01 M16 19h.01"})[name]);svg.append(path);return svg;}
  function renderMobileRoute(){const root=$("mobile-route");root.replaceChildren();if(state.route==="list"){root.append(document.querySelector("#app-home .app-mark").cloneNode(true),el("span","mobile-route-title","Inbox"));return;}const back=el("button","mobile-back","返回");back.type="button";back.prepend(navigationIcon("back"));back.addEventListener("click",backToList);const found=itemByKey(state.selected);const title=state.route==="edit"?(state.draft?.originalKey?"编辑事项":"新增事项"):(found?.item.title||"事项详情");root.append(back,el("span","mobile-route-title",title));}
  function renderProjectFilter() {
    const select=projectSelect, options=InboxProjects.options(state.sections);
    if(state.project!==null&&!options.some(([key])=>key===state.project))state.project=null;
    const addOption=(label,value,count)=>{
      const option=new Option(`${label} · ${count} 项`,value);
      option.dataset.projectLabel=label;option.dataset.count=String(count);select.append(option);
    };
    select.replaceChildren();
    addOption("全部项目","*",options.reduce((total,[,count])=>total+count,0));
    for(const [key,count] of options)addOption(InboxProjects.label(key),`project:${key}`,count);
    select.value=state.project===null?"*":`project:${state.project}`;
    select.disabled=state.route==="edit";
    enhanceSelect(select);
    ui.syncSelect(select);
    const trigger=$("project-filter"),selected=select.selectedOptions[0];
    trigger.dataset.count=selected.dataset.count;
    trigger.dataset.filtered=String(state.project!==null);
    trigger.title=select.disabled?"保存或取消编辑后可切换项目":selected.dataset.projectLabel;
    trigger.setAttribute("aria-description",select.disabled?"保存或取消编辑后可切换项目":`当前显示 ${selected.dataset.count} 项事项`);
  }
  function renderList() {
    const root=$("status-groups");disclosure.reset();disclosureRows.clear();root.replaceChildren();let total=0;
    InboxProjects.filter(state.sections,state.project).forEach(section=>{
      total+=section.items.length;
      const group=el("details","status-group");group.dataset.status=section.status;group.open=!collapsedStatuses.has(section.status);
      installDropTarget(group,()=>({status:section.status,edge:"end"}));
      group.addEventListener("toggle",()=>{if(group.open)collapsedStatuses.delete(section.status);else collapsedStatuses.add(section.status);});
      const head=el("summary","status-heading"),chevron=navigationIcon("next");chevron.classList.add("group-chevron");
      head.append(el("h2","",LABEL[section.status]),el("span","count",String(section.items.length)),chevron);group.append(head);
      if(!section.items.length)group.append(el("p","empty-list","没有事项"));
      const list=el("ul","entry-list");
      section.items.forEach(item=>{
        const li=el("li"),button=el("button","entry-card");button.type="button";button.dataset.entryKey=item.entryKey;
        button.setAttribute("aria-current",String(state.route!=="list"&&state.selected===item.entryKey));
        const summary=el("span","entry-summary"),original=el("span","entry-summary-original",item.summary);
        summary.append(original);button.append(el("span","entry-title",item.title),summary);
        if(item.closeoutPending?.length)button.append(el("span","entry-closeout",`待收尾 · ${item.closeoutPending.length} 项`));
        button.addEventListener("click",()=>{if(Date.now()>=suppressOpenUntil)openItem(item.entryKey);});li.append(button);
        if(item.workPath){
          const body=el("span","entry-preview");
          body.hidden=true;body.setAttribute("aria-hidden","true");
          summary.append(body);disclosureRows.set(item.entryKey,{button,body,item});
          button.addEventListener("pointermove",event=>{if(event.pointerType!=="touch")disclosure.move(item.entryKey,event.clientX,event.clientY);});
          li.addEventListener("pointerenter",()=>disclosure.enter(item.entryKey));
          li.addEventListener("pointerleave",()=>disclosure.leave(item.entryKey));
          li.addEventListener("focusout",event=>{if(!li.contains(event.relatedTarget))disclosure.blur(item.entryKey);});
          button.addEventListener("focus",()=>{if(button.matches(":focus-visible"))disclosure.focus(item.entryKey);});
        }
        li.draggable=state.route!=="edit";button.draggable=li.draggable;
        let rowDragAllowed=true;
        li.addEventListener("pointerdown",event=>{rowDragAllowed=!event.target.closest(".entry-actions");});
        li.addEventListener("dragstart",event=>{
          if(saving||state.route==="edit"||!rowDragAllowed){event.preventDefault();return;}
          dragging={key:item.entryKey,pinned:!!item.pinned};suppressOpenUntil=Infinity;disclosure.escape();closeLayer();
          event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",item.entryKey);
          li.classList.add("is-dragging");
        });
        li.addEventListener("dragend",clearDrag);
        installDropTarget(li,event=>({status:section.status,[event.clientY<li.getBoundingClientRect().top+li.offsetHeight/2?"beforeKey":"afterKey"]:item.entryKey}),item);
        const actions=el("div","entry-actions");actions.setAttribute("role","group");actions.setAttribute("aria-label",`${item.title}：操作`);
        const copy=copyForAI(item,section.status);copy.classList.add("entry-copy");
        const full=itemByKey(item.entryKey);
        actions.draggable=false;
        actions.append(copy,...orderButtons(full,`entry-${item.entryKey}`,true));
        li.append(actions);list.append(li);
      });
      group.append(list);root.append(group);
    });
    $("item-total").textContent=state.project===null?`${total} 项`:`${total} / ${state.sections.reduce((sum,section)=>sum+section.items.length,0)} 项`;
  }
  const readWork = path => jsonFetch(`/api/work-document?path=${encodeURIComponent(path)}`);
  const descriptionRequests=new WeakMap();
  async function loadDescription(body,item) {
    const request={};descriptionRequests.set(body,request);
    body.dataset.ready="false";body.textContent="";
    try{const doc=await readWork(item.workPath);if(!body.isConnected||body.hidden||descriptionRequests.get(body)!==request)return;
      const text=InboxProjects.descriptionText(doc);
      if(text){body.textContent=text;body.dataset.ready="true";}
    }catch(error){/* Keep the current summary when its optional preview cannot be read. */}
  }
  function appendDescription(body,doc,anchors=false) {
    const section=doc.sections?.description;
    if(!section?.blocks.length){body.append(el("p","hint","尚未记录事项说明，可进入详情阅读已有 Work。"));return;}
    section.blocks.forEach(block=>body.append(renderBlock(block,anchors)));
  }
  function copyForAI(item,status) {
    const button=el("button","icon-button"),feedback=el("span","sr-only");button.type="button";feedback.setAttribute("role","status");
    function showState(icon,label){button.replaceChildren(navigationIcon(icon),feedback);button.title=label;button.setAttribute("aria-label",`${item.title}：${label}`);}
    showState("copy","复制给 AI");
    button.addEventListener("click",async()=>{
      button.disabled=true;feedback.textContent="正在复制";
      try{
        const text=jsonFetch("/api/inbox").then(inbox=>InboxProjects.handoff(item.entryKey,inbox));
        if(window.ClipboardItem&&navigator.clipboard?.write){
          await navigator.clipboard.write([new ClipboardItem({"text/plain":text.then(value=>new Blob([value],{type:"text/plain"}))})]);
        }else await navigator.clipboard.writeText(await text);
        showState("check","已复制");feedback.textContent="已复制";
      }catch(error){showState("error",`复制失败：${error.message}`);feedback.textContent=`复制失败：${error.message}`;}
      finally{button.disabled=false;setTimeout(()=>{showState("copy","复制给 AI");feedback.textContent="";},2000);}
    });return button;
  }
  const disclosureRows=new Map();
  const disclosure=InboxProjects.disclosure({
    show(key){const row=disclosureRows.get(key);if(!row?.button.isConnected)return;row.body.hidden=false;row.button.parentElement.classList.add("is-reading");loadDescription(row.body,row.item);},
    hide(key){const row=disclosureRows.get(key);if(!row)return;row.body.hidden=true;row.button.parentElement.classList.remove("is-reading");}
  });
  document.addEventListener("scroll",()=>disclosure.scroll(),true);
  document.addEventListener("wheel",()=>disclosure.scroll(),{passive:true,capture:true});
  document.addEventListener("touchmove",()=>disclosure.scroll(),{passive:true,capture:true});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")disclosure.escape();});
  window.addEventListener("resize",()=>{disclosure.escape();disclosure.scroll();});

  function orderButtons(found,prefix="order",iconsOnly=false){
      const top=el("button","order-button","上移");top.type="button";top.id=`${prefix}-top`;top.prepend(navigationIcon("top"));
      top.title=found.item.pinned?"移到置顶事项最前面":"一次移到未置顶事项最前面";
      const band=found.section.items.filter(i=>!!i.pinned===!!found.item.pinned);
      top.disabled=state.route==="edit"||band[0]?.entryKey===found.item.entryKey;
      top.addEventListener("click",()=>arrange(found.item.entryKey,{status:found.section.status,edge:"start"},top.id));
      const pin=el("button","order-button",found.item.pinned?"取消置顶":"置顶");pin.type="button";pin.id=`${prefix}-pin`;
      pin.prepend(navigationIcon("pin"));pin.setAttribute("aria-pressed",String(!!found.item.pinned));
      pin.disabled=state.route==="edit";
      pin.title=found.item.pinned?"取消固定在区块顶部":"固定在当前区块顶部";
      pin.addEventListener("click",()=>arrange(found.item.entryKey,{pinned:!found.item.pinned},pin.id));
      if(iconsOnly){
        top.className="icon-button";top.replaceChildren(navigationIcon("top"));
        top.setAttribute("aria-label",`${found.item.title}：上移到最前`);
        pin.className="icon-button";pin.replaceChildren(navigationIcon("pin"));
        pin.setAttribute("aria-label",`${found.item.title}：${found.item.pinned?"取消置顶":"置顶"}`);
      }
      return [top,pin];
  }
  function toolbar(found,editing=false){
    const bar=el("div","detail-toolbar"),left=el("div","toolbar-status"),actions=el("div","toolbar-actions");
    if(!editing&&found){
      const label=el("label","","移到区块");label.htmlFor="quick-status";
      const select=el("select");select.id="quick-status";select.setAttribute("aria-label","移到区块");
      STATUS.forEach(value=>select.append(new Option(LABEL[value],value,false,value===found.section.status)));
      select.addEventListener("change",()=>arrange(found.item.entryKey,{status:select.value,edge:"end"},"quick-status"));
      left.append(label,select);
      const [top,pin]=orderButtons(found);
      const edit=el("button","icon-button");edit.type="button";edit.append(navigationIcon("edit"));edit.title="编辑事项";edit.setAttribute("aria-label","编辑事项");
      edit.addEventListener("click",()=>{state.draft={...found.item,status:found.section.status,originalKey:found.item.entryKey};state.localMessage="";route("edit");});
      actions.append(top,pin,copyForAI(found.item,found.section.status),edit);
    }
    bar.append(left,actions);
    if(state.localMessage){const feedback=el("span","local-feedback",state.localMessage);feedback.id="local-feedback";feedback.setAttribute("role","status");bar.append(feedback);}
    return bar;
  }
  const writable=({title,summary,issueUrl,workPath,itemId,pinned})=>({title,summary,issueUrl,workPath,pinned:!!pinned,...(itemId?{itemId}:{})});
  async function arrange(key,target,focusId){
    if(saving||state.route==="edit")return;
    const found=itemByKey(key);if(!found)return;
    let sections;
    try{sections='pinned' in target?InboxProjects.setPin(state.sections,key,target.pinned):InboxProjects.move(state.sections,key,target);}
    catch(error){showGlobalError({message:error.message,action:""});return;}
    if(JSON.stringify(sections)===JSON.stringify(state.sections))return;
    saving++;viewEpoch++;arrangementError=false;state.localMessage="";clearGlobalError();
    $("workspace").inert=true;$("new-entry").disabled=true;$("reload-inbox").disabled=true;
    $("order-feedback").textContent="正在保存顺序…";
    try{
      const data=await jsonFetch("/api/inbox",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({expectedRevision:state.revision,sections:sections.map(s=>({status:s.status,items:s.items.map(writable)}))})});
      state.revision=data.revision;state.sections=data.sections;
      const status=target.status||found.section.status;
      collapsedStatuses.delete(status);
      state.localMessage='pinned' in target?(target.pinned?"已置顶，固定在区块顶部":"已取消置顶"):target.edge==='start'?"已移到最前面":"顺序已保存";
      preserveView(()=>render());$("order-feedback").textContent=state.localMessage;$("sync-state").textContent="已同步";
    }catch(error){
      // No optimistic mutation: a stale save leaves the source and local order intact.
      arrangementError=true;state.localMessage="顺序未保存";
      render();showGlobalError({message:error.detail?.message||error.message,action:error.status===409?"顺序未保存，读取最新内容后再操作。":"顺序未保存，请重试。"});
      const refresh=el("button","text-button","读取最新内容");refresh.type="button";refresh.addEventListener("click",()=>load({preserveRoute:true}));$("app-error").append(refresh);
      $("order-feedback").textContent="顺序未保存";
    }finally{
      saving--;viewEpoch++;$("workspace").inert=false;$("new-entry").disabled=false;$("reload-inbox").disabled=false;
      if($("app-error").hidden){const control=focusId&&$(focusId);(control&&!control.disabled?control:document.querySelector(`[data-entry-key="${CSS.escape(key)}"]`))?.focus({preventScroll:true});}
    }
  }
  function clearDropMarks(){document.querySelectorAll('[data-drop-position]').forEach(node=>delete node.dataset.dropPosition);}
  function clearDrag(){if(dragging)suppressOpenUntil=Date.now()+350;dragging=null;clearDropMarks();document.querySelectorAll('.is-dragging').forEach(node=>node.classList.remove('is-dragging'));}
  function installDropTarget(node,targetFor,item=null){
    const accepts=()=>dragging&&!saving&&state.route!=="edit"&&(!item||(item.entryKey!==dragging.key&&!!item.pinned===dragging.pinned));
    node.addEventListener("dragenter",event=>{if(accepts()){event.preventDefault();event.stopPropagation();}});
    node.addEventListener("dragover",event=>{
      if(!dragging)return;event.stopPropagation();clearDropMarks();
      if(!accepts())return;event.preventDefault();event.dataTransfer.dropEffect="move";
      const target=targetFor(event);node.dataset.dropPosition=target.beforeKey?'before':target.afterKey?'after':'section';
    });
    node.addEventListener("dragleave",event=>{if(!node.contains(event.relatedTarget))delete node.dataset.dropPosition;});
    node.addEventListener("drop",event=>{
      if(!dragging)return;event.stopPropagation();event.preventDefault();
      const key=dragging.key,accepted=accepts(),target=targetFor(event);clearDrag();
      if(accepted)arrange(key,target);
    });
  }
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&dragging){clearDrag();$("order-feedback").textContent="已取消拖动";}});

  function propertyRow(label,value){const row=el("div","property-row");row.append(el("dt","",label),value);return row;}
  function renderLifecycle(root,item){
    if(item.arrangementNote)root.append(el("p","work-status",`个人安排：${item.arrangementNote}`));
    const labels={unverified:"待核实",planned:"待开始",active:"进行中",paused:"已暂停",completed:"已完成",cancelled:"已取消"};
    if(item.lifecycleStatus){
      const status=el("p","work-status",`工作进展：${labels[item.lifecycleStatus]||"待核实"}`);
      status.dataset.lifecycle=item.lifecycleStatus;root.append(status);
    }
    if(item.closeoutPending?.length){
      const details=el("details","closeout-pending"),summary=el("summary","",`待收尾 · ${item.closeoutPending.length} 项`),list=el("ul");
      item.closeoutPending.forEach(reason=>list.append(el("li","",reason)));
      details.append(summary,list);root.append(details);
    }
  }
  function renderDetail(){
    const root=$("detail-content");root.replaceChildren();if(state.route==="edit"){renderForm(root);return;}
    const found=itemByKey(state.selected);if(!found){const empty=el("div","empty-detail"),heading=el("h2","","选择一个事项");heading.id="detail-heading";empty.append(heading);root.append(empty);return;}
    root.append(toolbar(found));const header=el("header","detail-header"),title=el("h1","",found.item.title);title.id="detail-heading";header.append(title);renderLifecycle(header,found.item);root.append(header);
    if(found.item.workPath)renderWork(root,found.item.workPath,found.item.entryKey);
    else root.append(el("h2","","当前推进"),el("p","detail-summary",found.item.summary));
    if(found.item.issueUrl){const link=el("a","issue-source","关联 Issue");link.href=found.item.issueUrl;link.target="_blank";link.rel="noopener noreferrer";root.append(link);}
  }

  function field(form,id,label,type,value,hint,parent=form){const wrap=el("div","field"),lab=el("label","",label);lab.htmlFor=id;const input=type==="textarea"?el("textarea"):type==="select"?el("select"):el("input");input.id=id;input.name=id;if(type!=="textarea"&&type!=="select")input.type=type;input.value=value||"";wrap.append(lab,input);if(hint)wrap.append(el("p","hint",hint));parent.append(wrap);return input;}
  function renderForm(root){const draft=state.draft||{title:"",summary:"",status:"Needs you",issueUrl:"",workPath:"",originalKey:null};state.draft=draft;const found=draft.originalKey?itemByKey(draft.originalKey):null;root.append(toolbar(found,true));const form=el("form","entry-form");form.noValidate=true;const heading=el("h1","",draft.originalKey?"编辑事项":"新增事项");heading.id="detail-heading";form.append(heading);const feedback=el("div","form-feedback");feedback.id="form-feedback";feedback.setAttribute("role","alert");feedback.tabIndex=-1;feedback.hidden=true;form.append(feedback);const title=field(form,"entry-title","标题","text",draft.title,"保持简短、唯一。");title.required=true;const summary=field(form,"entry-summary","当前摘要","textarea",draft.summary,"写清现在成立什么，以及下一步是什么。");summary.required=true;const compact=el("div","form-properties");const status=field(form,"entry-status","个人分类","select",draft.status,"用于安排你的关注，不会完成工作或清理资源。",compact);STATUS.forEach(value=>status.append(new Option(LABEL[value],value,false,value===draft.status)));const issue=field(form,"entry-issue","Issue","url",draft.issueUrl,"仅接受不含凭据的 HTTPS URL。",compact);const work=field(form,"entry-work","Work 文档","select",draft.workPath,"只读取允许目录中的 Markdown。",compact);work.append(new Option("不关联",""));state.work.forEach(item=>work.append(new Option(`${item.state} · ${item.project} · ${item.title}`,item.path,false,item.path===draft.workPath)));form.append(compact);const actions=el("div","form-actions"),save=el("button","primary",draft.originalKey?"保存":"新增"),cancel=el("button","text-button","取消");save.type="submit";cancel.type="button";cancel.addEventListener("click",()=>{if(saving)return;state.draft=null;if(draft.originalKey)route("detail",{replace:true});else{state.selected=null;route("list",{replace:true});}});actions.append(save,cancel);form.append(actions);const remember=()=>{state.draft={title:title.value,summary:summary.value,status:status.value,issueUrl:issue.value,workPath:work.value,itemId:draft.itemId,originalKey:draft.originalKey};};[title,summary,status,issue,work].forEach(node=>node.addEventListener("input",remember));form.addEventListener("submit",async event=>{event.preventDefault();remember();clearFieldErrors(form);const invalid=validateDraft(state.draft);if(invalid){fieldError(form,invalid.field,invalid.message);return;}save.disabled=true;save.textContent="保存中…";try{await persistItem(state.draft);state.localMessage=draft.originalKey?"已保存":"已新增";state.draft=null;route("detail",{replace:true});}catch(failure){showFormError(form,failure.detail||{message:failure.message,action:"请检查后重试。"});if(failure.status===409)addConflictRecovery(form);const names={title:"entry-title",summary:"entry-summary",issueUrl:"entry-issue",workPath:"entry-work",status:"entry-status"};if(failure.detail?.field)fieldError(form,names[failure.detail.field]||"entry-title",failure.detail.message,false);}finally{save.disabled=false;save.textContent=draft.originalKey?"保存":"新增";}});root.append(form);requestAnimationFrame(()=>{if(!form.contains(document.activeElement))title.focus();});}
  function clearFieldErrors(form){const feedback=form.querySelector("#form-feedback");feedback.hidden=true;feedback.replaceChildren();form.querySelectorAll("[aria-invalid]").forEach(node=>{node.removeAttribute("aria-invalid");node.removeAttribute("aria-describedby");});form.querySelectorAll(".field-error").forEach(node=>node.remove());}
  function showFormError(form,error,fieldId){const feedback=form.querySelector("#form-feedback");feedback.replaceChildren(el("strong","",error.message||"无法保存。"));if(error.action)feedback.append(el("span","",error.action));if(fieldId){const link=el("a","","前往需要修改的字段");link.href=`#${fieldId}`;link.addEventListener("click",event=>visitFragment(event,link.hash));feedback.append(link);}feedback.hidden=false;feedback.focus();}
  function addConflictRecovery(form) {
    const feedback=form.querySelector("#form-feedback");
    const recover=el("button","text-button","读取最新版本并保留草稿");recover.type="button";
    recover.addEventListener("click",async()=>{
      recover.disabled=true;
      try {
        const inbox=await jsonFetch("/api/inbox"),draft=state.draft;
        if(state.route!=="edit"||!draft||!form.isConnected)return;
        const original=draft.originalKey?itemByKey(draft.originalKey):null;
        let latest=draft.originalKey?itemByKeyIn(inbox.sections,draft.originalKey):null;
        if(!latest&&original&&!original.item.itemId){
          for(const section of inbox.sections){const item=section.items.find(item=>item.title===original.item.title);if(item){latest={section,item};break;}}
        }
        if(draft.originalKey&&!latest){
          showFormError(form,{message:"原事项已被删除或改名，草稿仍保留。",action:"请先复制当前输入，核对最新 Inbox；不会将它自动新建或覆盖其他事项。"});
          return;
        }
        if(latest){draft.originalKey=latest.item.entryKey;draft.itemId=latest.item.itemId;state.selected=latest.item.entryKey;}
        state.revision=inbox.revision;state.sections=inbox.sections;
        render();
        const current=document.querySelector(".entry-form");
        showFormError(current,{message:"已读取最新版本，你的草稿已保留，尚未保存。",action:"请对照下面的最新内容修改草稿；再次保存将使用当前输入更新此事项。"});
        const panel=current.querySelector("#form-feedback");
        if(latest){
          const copy=el("div","conflict-latest");
          copy.append(el("strong","","最新事项内容"));
          for(const [label,value] of [["标题",latest.item.title],["摘要",latest.item.summary],["个人分类",LABEL[latest.section.status]],["Issue",latest.item.issueUrl||"未关联"],["Work",latest.item.workPath||"未关联"]])copy.append(el("p","",`${label}：${value}`));
          panel.append(copy);
        }
      }catch(error){
        if(form.isConnected){showFormError(form,{message:"读取最新版本失败，草稿仍保留。",action:error.message});addConflictRecovery(form);}
      }finally{recover.disabled=false;}
    });
    feedback.append(recover);
  }
  function fieldError(form,id,message,focus=true){const input=form.querySelector(`#${id}`);if(!input)return;const note=el("p","field-error",message);note.id=`${id}-error`;input.setAttribute("aria-invalid","true");input.setAttribute("aria-describedby",note.id);input.closest(".field").append(note);showFormError(form,{message:"请修正以下问题。",action:message},id);if(focus)form.querySelector("#form-feedback").focus();}
  function validateDraft(d){if(!d.title.trim())return{field:"entry-title",message:"请输入标题。"};if(!d.summary.trim())return{field:"entry-summary",message:"请输入当前摘要。"};if(d.issueUrl){try{const url=new URL(d.issueUrl);if(url.protocol!=="https:"||url.username||url.password)throw new Error();}catch(_){return{field:"entry-issue",message:"请输入不含凭据的完整 HTTPS URL。"};}}return null;}
  async function persistItem(d){
    const original=d.originalKey?itemByKey(d.originalKey):null;
    if(d.originalKey&&!original)throw new Error("原事项已发生变化，请重新读取后核对草稿。");
    const changed=writable({title:d.title.trim(),summary:d.summary.trim(),issueUrl:d.issueUrl.trim(),workPath:d.workPath,pinned:original?.item.pinned||false,itemId:d.itemId||original?.item.itemId});
    const sections=state.sections.map(section=>({status:section.status,items:section.items.flatMap(item=>item.entryKey!==d.originalKey?[writable(item)]:section.status===d.status?[changed]:[])}));
    if(!original||original.section.status!==d.status)sections.find(section=>section.status===d.status).items.push(changed);
    saving++;viewEpoch++;
    try {
    const data=await jsonFetch("/api/inbox",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({expectedRevision:state.revision,sections})});
    state.revision=data.revision;state.sections=data.sections;
    const saved=state.sections.find(section=>section.status===d.status).items.find(item=>changed.itemId?item.itemId===changed.itemId:item.title===changed.title);
    if(state.project!==null)state.project=InboxProjects.key(saved);
    state.selected=saved.entryKey;state.restoreKey=saved.entryKey;$("sync-state").textContent="已同步";return saved;
    } finally {saving--;viewEpoch++;}
  }

  async function renderWork(root,path,entryKey){const shell=el("section","work-shell"),loading=el("p","work-loading","正在读取关联 Work 文档…");shell.append(loading);root.append(shell);try{if(preparedWork?.path===path&&preparedWork.error)throw preparedWork.error;const doc=preparedWork?.path===path?preparedWork.doc:await jsonFetch(`/api/work-document?path=${encodeURIComponent(path)}`);if(state.selected!==entryKey||!shell.isConnected)return;shell.replaceChildren();renderWorkDoc(shell,doc);}catch(error){const message=error.detail?.message||error.message,action=error.detail?.action||"检查关联路径后重试。";shell.replaceChildren(el("p","work-error",`${message} ${action}`));}}
  function visitFragment(event,target){event.preventDefault();const id=decodeURIComponent(target.slice(1)),heading=document.getElementById(id);if(!heading)return;for(let parent=heading.parentElement;parent;parent=parent.parentElement)if(parent.tagName==="DETAILS")parent.open=true;heading.scrollIntoView({block:"start"});if(!heading.matches("a[href],button,input,select,textarea,[tabindex]"))heading.tabIndex=-1;heading.focus({preventScroll:true});}
  function renderInline(parent,nodes){nodes.forEach(node=>{if(node.type==="text")parent.append(document.createTextNode(node.text));else if(node.type==="code")parent.append(el("code","",node.text));else if(node.type==="strong")parent.append(el("strong","",node.text));else if(node.type==="emphasis")parent.append(el("em","",node.text));else if(node.type==="link"){if(node.kind==="external"){const a=el("a","",node.text);a.href=node.target;a.target="_blank";a.rel="noopener noreferrer";parent.append(a);}else if(node.kind==="fragment"){const a=el("a","",node.text);a.href=node.target;a.addEventListener("click",event=>visitFragment(event,node.target));parent.append(a);}else parent.append(el("span",node.kind==="unsafe"?"unsafe-link":"source-reference",`${node.text} (${node.target})`));}});}
  function outlineNav(doc){const nav=el("nav");nav.setAttribute("aria-label","Work 文档章节");doc.outline.forEach(item=>{const a=el("a","",item.title);a.href=`#${item.id}`;a.dataset.level=item.level;a.addEventListener("click",event=>visitFragment(event,a.hash));nav.append(a);});return nav;}
  function renderWorkDoc(shell,doc){
    const article=el("article","work-document"),descriptionHeading=el("h2","","事项说明"),progressHeading=el("h2","","当前推进");
    if(doc.sections?.description?.id)descriptionHeading.id=doc.sections.description.id;
    if(doc.sections?.progress?.id)progressHeading.id=doc.sections.progress.id;
    article.append(descriptionHeading);appendDescription(article,doc,true);
    article.append(progressHeading);
    const progress=doc.sections?.progress;
    if(progress?.blocks.length)progress.blocks.forEach(block=>article.append(renderBlock(block)));
    else article.append(el("p","",itemByKey(state.selected)?.item.summary||"尚未记录当前推进。"));
    shell.append(article);
    const details=el("details","work-evidence"),summary=el("summary","","执行细节与来源"),body=el("article","work-document");
    (doc.remainingBlocks||doc.blocks).forEach(block=>body.append(renderBlock(block)));
    const source=el("p","property-secondary",doc.absolutePath||doc.path);details.append(summary,body,source);shell.append(details);
  }
  function renderBlock(block,anchors=true){if(block.type==="heading"){const h=el(`h${block.level}`);if(anchors)h.id=block.id;renderInline(h,block.inlines);return h;}if(block.type==="paragraph"){const p=el("p");renderInline(p,block.inlines);return p;}if(block.type==="blockquote"){const q=el("blockquote");renderInline(q,block.inlines);return q;}if(block.type==="code"){const pre=el("pre"),code=el("code","",block.text);if(block.language)code.dataset.language=block.language;pre.append(code);return pre;}if(block.type==="frontmatter")return el("pre","frontmatter",block.text);if(block.type==="thematicBreak")return el("hr");if(block.type==="list"){const list=el(block.ordered?"ol":"ul"),parents=[list];block.items.forEach(item=>{while(parents.length-1>item.depth)parents.pop();while(parents.length-1<item.depth){const nested=el(block.ordered?"ol":"ul"),parent=parents.at(-1),last=parent.lastElementChild||parent.appendChild(el("li"));last.append(nested);parents.push(nested);}const li=el("li");renderInline(li,item.inlines);parents.at(-1).append(li);});return list;}return el("p","",String(block.text||""));}

  const statusHelp = {'Needs you':'需要你关注或决定','In progress':'放入你当前关注的工作','Waiting':'留待后续关注与安排','Done':'移出当前关注；工作完成与资源收尾单独记录'};
  const ui = WorkspaceUI.create({selectOption(option) {
    if(option.parentElement===projectSelect)return {label:option.dataset.projectLabel,description:option.dataset.count};
    if(STATUS.includes(option.value))return {tone:({'Needs you':'amber','In progress':'blue','Waiting':'purple','Done':'green'})[option.value],description:statusHelp[option.value]};
    return {label:workByPath(option.value)?.title,description:option.value || '此事项不关联工作文档'};
  }});
  const {closeLayer,enhanceSelect} = ui;

  function render(){viewEpoch++;closeLayer();document.body.dataset.route=state.route;const skip=document.querySelector(".skip-link");skip.href=state.route==="list"?"#list-heading":"#detail-panel";skip.textContent=state.route==="list"?"跳到事项列表":"跳到事项详情";renderMobileRoute();renderProjectFilter();renderList();renderDetail();document.querySelectorAll("select:not([hidden]):not(#project-filter)").forEach(enhanceSelect);}
  function interactionBusy(){
    return document.hidden||loading||saving||dragging||state.route==="edit"||Date.now()-lastScroll<400||
      !!document.querySelector('.select-menu:not([hidden])')||!!window.getSelection()?.toString()||
      !!document.querySelector('.entry-copy:disabled');
  }
  function preserveView(update){
    const active=document.activeElement, card=active?.closest('li')?.querySelector('[data-entry-key]');
    const focus=active?.id?()=>$(active.id):card?()=>{
      const next=document.querySelector(`[data-entry-key="${CSS.escape(card.dataset.entryKey)}"]`);
      return active.classList.contains('entry-copy')?next?.parentElement.querySelector('.entry-copy'):next;
    }:null;
    const panel=$("list-panel"),detail=$("detail-panel"),top=desktop()?panel.getBoundingClientRect().top:document.querySelector('.app-bar').getBoundingClientRect().bottom;
    const anchor=[...document.querySelectorAll('.status-group[open] [data-entry-key]')].find(node=>node.getBoundingClientRect().bottom>top);
    const anchorTop=anchor?.getBoundingClientRect().top, scroll=[panel.scrollTop,detail.scrollTop,window.scrollX,window.scrollY];
    const expanded=[...document.querySelectorAll('#detail-content details')].map(node=>node.open);
    update();
    document.querySelectorAll('#detail-content details').forEach((node,index)=>{node.open=expanded[index]||false;});
    panel.scrollTop=scroll[0];detail.scrollTop=scroll[1];window.scrollTo(scroll[2],scroll[3]);
    const next=anchor&&document.querySelector(`[data-entry-key="${CSS.escape(anchor.dataset.entryKey)}"]`);
    if(next){const delta=next.getBoundingClientRect().top-anchorTop;if(desktop())panel.scrollTop+=delta;else if(state.route==="list")window.scrollTo(scroll[2],scroll[3]+delta);}
    if(active&&!active.isConnected)(focus?.()||$(state.route==="list"?"list-heading":"detail-panel")).focus({preventScroll:true});
  }
  const live=InboxProjects.refreshQueue({
    epoch:()=>viewEpoch,blocked:interactionBusy,status:text=>{$("sync-state").textContent=text;},
    async load(version){
      const data=await jsonFetch(`/api/snapshot?version=${encodeURIComponent(version)}`);
      if(!data.unchanged){
        const selected=itemByKeyIn(data.inbox.sections,state.selected)?.item;
        if(selected?.workPath&&(JSON.stringify(itemView(itemByKey(state.selected)))!==JSON.stringify(itemView(itemByKeyIn(data.inbox.sections,state.selected)))||workVersions[selected.workPath]!==data.workVersions[selected.workPath])){
          try{data.preparedWork={path:selected.workPath,doc:await readWork(selected.workPath)};}
          catch(error){data.preparedWork={path:selected.workPath,error};}
        }
      }
      return data;
    },
    apply(data){
      if(data.unchanged)return;
      const before=itemByKey(state.selected),after=itemByKeyIn(data.inbox.sections,state.selected);
      const listChanged=JSON.stringify(state.sections)!==JSON.stringify(data.inbox.sections);
      const detailChanged=JSON.stringify(itemView(before))!==JSON.stringify(itemView(after))||workVersions[after?.item.workPath]!==data.workVersions[after?.item.workPath];
      preserveView(()=>{
        state.revision=data.inbox.revision;state.sections=data.inbox.sections;state.work=data.documents;
        preparedWork=data.preparedWork||null;
        if(state.selected&&!after){state.selected=null;state.route="list";history.replaceState({route:"list",key:null},"","#inbox");render();}
        else{
          if(listChanged){renderProjectFilter();renderList();}
          else for(const row of disclosureRows.values())if(workVersions[row.item.workPath]!==data.workVersions[row.item.workPath]){row.body.dataset.ready="false";if(!row.body.hidden)loadDescription(row.body,row.item);}
          if(detailChanged){renderMobileRoute();renderDetail();document.querySelectorAll("select:not([hidden]):not(#project-filter)").forEach(enhanceSelect);}
        }
        preparedWork=null;workVersions=data.workVersions;liveVersion=data.version;
      });
      if(!arrangementError)clearGlobalError();
    }
  });
  let events=null;
  function connectLive(){
    events?.close();events=null;
    if(document.hidden)return;
    events=new EventSource('/api/events');
    events.onmessage=event=>{
      const change=JSON.parse(event.data);
      if(change.unavailable){$("sync-state").textContent="数据暂不可用，等待恢复";return;}
      if(change.version!==liveVersion&&state.route==="edit")$("sync-state").textContent="后台有更新，草稿已保留";
      live.notify(change.version);
    };
    events.onerror=()=>{$("sync-state").textContent="连接中断，正在重连";};
    // Refresh once on return/reconnect, even when the last version is unchanged.
    events.onopen=()=>live.notify();
  }
  document.addEventListener('scroll',()=>{lastScroll=Date.now();},true);
  document.addEventListener('visibilitychange',connectLive);
  window.addEventListener('pagehide',()=>events?.close());
  window.addEventListener('pageshow',connectLive);
  document.querySelector(".skip-link").addEventListener("click",event=>visitFragment(event,event.currentTarget.hash));
  function routeFromLocation(){const match=location.hash.match(/^#detail\/([^/?#]+)$/);return match?{route:"detail",key:decodeURIComponent(match[1])}:{route:"list",key:null};}
  projectSelect.addEventListener("change",event=>{
    state.project=event.target.value==="*"?null:event.target.value.slice("project:".length);
    const selected=itemByKey(state.selected);
    if(selected&&state.project!==null&&InboxProjects.key(selected.item)!==state.project){
      state.selected=null;state.localMessage="";route("list",{replace:true,focus:false});
    }else render();
  });
  $("new-entry").addEventListener("click",beginNew);$("reload-inbox").addEventListener("click",()=>load({preserveRoute:true}));$("app-home").addEventListener("click",event=>{event.preventDefault();backToList();});window.addEventListener("beforeunload",event=>{if(state.route==="edit"){event.preventDefault();event.returnValue="";}});window.addEventListener("popstate",event=>{if(!mayLeaveEdit()){history.pushState({route:state.route,key:state.selected},"",`#${state.route}/${state.selected||"new"}`);return;}const next=event.state?.route||"list";state.selected=next==="list"?null:event.state?.key||null;state.draft=null;state.localMessage="";route(next,{push:false});});const initialRoute=routeFromLocation();state.route=initialRoute.route;state.selected=initialRoute.key;history.replaceState(initialRoute,"",initialRoute.route==="detail"?`#detail/${encodeURIComponent(initialRoute.key)}`:"#inbox");load({preserveRoute:true}).then(connectLive);
})();
