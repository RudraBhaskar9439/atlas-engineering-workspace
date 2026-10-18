export function citationLinks(text, nodes) {
  const sources=new Map(nodes.filter(n=>n.kind==='Source').map(n=>[n.id,n]));
  return text.replace(/\[([^\]\n]+)\](?!\()/g,(full,body)=>{
    const refs=[...body.matchAll(/\b([A-Za-z0-9_-]+)@(\d+)\b/g)];
    if(!refs.length)return full;
    return refs.map(([,id,version])=>{
      const revision=Number(version),source=sources.get(id);
      return source&&revision>0&&revision<=source.revision?`[${id}@${revision}](#source=${encodeURIComponent(id)}&revision=${revision})`:`${id}@${revision} (unverified reference)`;
    }).join(', ');
  });
}
