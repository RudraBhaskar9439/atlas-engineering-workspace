let csrf = '';
export function setCSRF(value:string){csrf=value||'';}
export async function api(path:string, options:RequestInit={}){
  const headers:any={...options.headers};
  if(options.body && !(options.body instanceof FormData))headers['Content-Type']='application/json';
  if(options.method && options.method!=='GET')headers['X-CSRF-Token']=csrf;
  const r=await fetch(path,{...options,headers,credentials:'same-origin'});
  const data=await r.json().catch(()=>({detail:'Server returned an unreadable response'}));
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Request could not be completed');
  return data;
}
