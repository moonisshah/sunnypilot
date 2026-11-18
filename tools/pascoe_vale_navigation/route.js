const {nodes,roads,rules,bounds,addresses}=data;
const edges=[],wayNames=new Map(),circles=new Set();
for(const [way,name,ids,direction,circle] of roads){
 wayNames.set(way,name);if(circle)circles.add(way);
 for(let i=1;i<ids.length;i++){const a=ids[i-1],b=ids[i];if(a!==b)edges.push({a,b,way,direction});}
}
function project(p,a,b){
 const dx=b[0]-a[0],dy=b[1]-a[1],len=dx*dx+dy*dy;
 const u=len?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/len)):0;
 const xy=[a[0]+u*dx,a[1]+u*dy];return {u,xy,d:Math.hypot(xy[0]-p[0],xy[1]-p[1])};
}
function snap(p){
 let best=null;
 for(const edge of edges){const q=project(p,nodes[edge.a],nodes[edge.b]);if(!best||q.d<best.d)best={...q,edge};}
 return best;
}
function legal(from,via,to,prev,next){return !rules.some(([f,v,t,rule])=>f===from&&v===via&&(rule.startsWith('only_')?t!==to:t===to&&(rule!=='no_u_turn'||prev===next)));}
function calculate(start, dest){
 if(!dest.every((x,i)=>Number.isFinite(x)&&x>=bounds[0][i]&&x<=bounds[1][i]))throw Error('Choose a point inside the local map.');
 const ss=snap(start),dd=snap(dest);if(Math.max(ss.d,dd.d)>100)throw Error('Choose a point within 100 m of a mapped public road.');
 const pts={...nodes},graph=new Map();
 function split(q,id){if(q.u<=1e-8)return q.edge.a;if(q.u>=1-1e-8)return q.edge.b;pts[id]=q.xy;return id;}
 const source=split(ss,-1),target=split(dd,-2);
 const add=(a,b,way,len)=>{if(!graph.has(a))graph.set(a,[]);graph.get(a).push({to:b,way,len});};
 for(const e of edges){let chain=[[0,e.a],[1,e.b]];
  if(e===ss.edge&&source<0)chain.push([ss.u,source]);if(e===dd.edge&&target<0)chain.push([dd.u,target]);chain.sort((a,b)=>a[0]-b[0]);
  for(let i=1;i<chain.length;i++){const a=chain[i-1][1],b=chain[i][1],len=Math.hypot(pts[a][0]-pts[b][0],pts[a][1]-pts[b][1]);if(e.direction>=0)add(a,b,e.way,len);if(e.direction<=0)add(b,a,e.way,len);}
 }
 let queue=[{id:source,cost:0}],seen=new Map(),end;
 while(queue.length){queue.sort((a,b)=>b.cost-a.cost);const q=queue.pop();if(q.id===target){end=q;break;}
  const key=[q.id,q.way,q.parent?.id].join('/');if(seen.has(key))continue;seen.set(key,true);
  for(const e of graph.get(q.id)||[]){if(q.parent?.id===e.to||!legal(q.way,q.id,e.way,q.parent?.id,e.to))continue;
   queue.push({id:e.to,parent:q,way:e.way,cost:q.cost+e.len});}}
 if(!end)throw Error('No connected route to this road in the local map.');
 const steps=[];for(let q=end;q;q=q.parent)steps.unshift(q);
 const route=steps.map(q=>pts[q.id]),turns=[];
 for(let i=1;i+1<steps.length;i++){const a=steps[i],b=steps[i+1];if(a.way===b.way)continue;
 const street=wayNames.get(b.way);if(circles.has(b.way)){if(!circles.has(a.way))turns.push([a.cost,'Enter the roundabout']);continue;}
 if(circles.has(a.way)){turns.push([a.cost,'Exit onto '+street]);continue;}
 const before=route[i-1],at=route[i],after=route[i+1],x=at[0]-before[0],y=at[1]-before[1],u=after[0]-at[0],v=after[1]-at[1];
 const angle=Math.atan2(x*v-y*u,x*u+y*v)*180/Math.PI;if(Math.abs(angle)<30&&wayNames.get(a.way)===street)continue;
 turns.push([a.cost,(Math.abs(angle)<30?'Continue onto ':angle>0?'Turn left onto ':'Turn right onto ')+street]);}
 turns.push([end.cost,'Reach destination']);return {route,turns,distance:end.cost,snap:dd.d,street:wayNames.get(dd.edge.way)};
}
