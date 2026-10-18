import React, {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, FileText, FolderGit2, Loader2, MessageSquare} from 'lucide-react';
import {api} from './api';

export default function GitHubIssues({repositories, canImport, onSaved, onInspect, onInvestigate, onCreateChange, creatingChange}: any) {
  const [repository, setRepository] = useState('https://github.com/falkordb/falkordb');
  const [state, setState] = useState('open');
  const [result, setResult] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [saved, setSaved] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const sequence = useRef(0);
  useEffect(() => () => {sequence.current++;}, []);

  async function browse(page = 1) {
    const current = ++sequence.current;
    setBusy('browse'); setError(''); setResult(null); setDetail(null); setSaved(null);
    try {
      const data = await api(`/api/github/issues?repository=${encodeURIComponent(repository)}&state=${state}&page=${page}`);
      if (current === sequence.current) setResult(data);
    } catch (e: any) {
      if (current === sequence.current) setError(e.message);
    } finally {
      if (current === sequence.current) setBusy('');
    }
  }

  async function openIssue(number: number) {
    const current = ++sequence.current;
    setBusy('detail'); setError(''); setDetail(null); setSaved(null);
    try {
      const data = await api(`/api/github/issues/${number}?repository=${encodeURIComponent(result.repository.name)}`);
      if (current === sequence.current) setDetail(data);
    } catch (e: any) {
      if (current === sequence.current) setError(e.message);
    } finally {
      if (current === sequence.current) setBusy('');
    }
  }

  async function saveIssue() {
    const current = ++sequence.current;
    setBusy('save'); setError('');
    try {
      const data = await api(`/api/github/issues/${detail.issue.number}/import`, {
        method: 'POST', body: JSON.stringify({repository: detail.repository.name}),
      });
      if (current !== sequence.current) return;
      await onSaved();
      if (current !== sequence.current) return;
      setDetail(data); setSaved(data);
    } catch (e: any) {
      if (current === sequence.current) setError(e.message);
    } finally {
      if (current === sequence.current) setBusy('');
    }
  }

  return <section className="panel github-issues">
    <div className="panel-heading"><div><FolderGit2 size={18}/><h3>Repository issues</h3></div><span className="badge">Public access</span></div>
    <p>Read the issue, inspect its discussion, and choose your next step. Browsing and saving evidence use no AI credits.</p>
    <form className="github-toolbar" onSubmit={e => {e.preventDefault(); browse();}}>
      <label>Public repository<input aria-label="GitHub issues repository" value={repository} disabled={!!busy} onChange={e => setRepository(e.target.value)} placeholder="https://github.com/owner/repository"/></label>
      <label>Issue state<select aria-label="GitHub issue state" value={state} disabled={!!busy} onChange={e => setState(e.target.value)}><option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option></select></label>
      <button className="button" disabled={!!busy || !repository.trim()}>{busy === 'browse' ? <Loader2 className="spin" size={15}/> : <MessageSquare size={15}/>}Browse issues</button>
    </form>
    {repositories.length > 0 && <label className="github-repository-picker">Use an imported repository<select aria-label="Imported repository for GitHub issues" value="" disabled={!!busy} onChange={e => {if(e.target.value) setRepository(e.target.value);}}><option value="">Select repository…</option>{repositories.map((r: any) => <option key={r.id} value={r.properties.url}>{r.title}</option>)}</select></label>}
    {error && <div className="alert" role="alert">{error}</div>}
    {result && <div className="github-browser">
      <div className="github-issue-list"><h4>{result.repository.name}</h4>
        {result.issues.length === 0 && <p>No issues on this page. GitHub also paginates pull requests; try another page.</p>}
        {result.issues.map((issue: any) => <button type="button" className={'github-issue-row ' + (detail?.issue.number === issue.number ? 'selected' : '')} key={issue.number} disabled={!!busy} onClick={() => openIssue(issue.number)}><b>#{issue.number} · {issue.title}</b><small>{issue.state} · {issue.author} · {issue.comments_count} comments</small><span>{issue.labels.slice(0, 3).join(' · ')}</span></button>)}
        <div className="github-pagination"><button className="button" disabled={!!busy || result.page <= 1} onClick={() => browse(result.page - 1)}>Previous</button><span>Page {result.page}</span><button className="button" disabled={!!busy || !result.has_next || result.page >= 100} onClick={() => browse(result.page + 1)}>Next</button></div>
      </div>
      <div className="github-issue-detail" aria-live="polite">
        {busy === 'detail' && <p><Loader2 className="spin" size={15}/> Loading issue and comments…</p>}
        {!detail && busy !== 'detail' && <p>Select an issue to read its description and comments.</p>}
        {detail && <><h3>#{detail.issue.number} · {detail.issue.title}</h3><p>{detail.issue.state} · reported by {detail.issue.author} · assigned to {detail.issue.assignees.join(', ') || 'nobody'}</p><a href={detail.issue.url} target="_blank" rel="noreferrer">Original GitHub issue <ArrowUpRight size={13}/></a>
          <div className="github-actions">{onCreateChange&&<button className="button primary" disabled={!!busy||!canImport||creatingChange} onClick={()=>onCreateChange(detail)}>{creatingChange?<Loader2 className="spin" size={15}/>:<FolderGit2 size={15}/>}Open change workspace</button>}<button className="button" disabled={!!busy || !canImport} onClick={saveIssue}>{busy === 'save' ? <Loader2 className="spin" size={15}/> : <FileText size={15}/>}Save to knowledge</button>
            {saved && <><button className="button" disabled={!!busy} onClick={() => onInspect(saved.source_id)}>View evidence · r{saved.revision}</button><button className="button primary" disabled={!!busy || !canImport} onClick={() => onInvestigate(`Investigate GitHub issue ${detail.repository.name}#${detail.issue.number} using ${saved.source_id}@${saved.revision}. Explain the reported bug, cite the available evidence, identify relevant indexed code and missing information, and propose a fix and tests. Distinguish proposed work from executed work.`)}>Ask Atlas about this <ArrowUpRight size={14}/></button></>}
          </div>
          {saved && <p className="muted" role="status">{saved.changed ? 'Evidence saved' : 'Evidence unchanged'} · {saved.source_id}@{saved.revision}</p>}
<div className="github-issue-body">{detail.issue.body || 'No description provided.'}</div>{detail.issue.body_truncated && <p className="muted">Description limited to 12,000 characters.</p>}
          <h4>Comments · showing {detail.comments.length} of {detail.issue.comments_count}</h4>
          {detail.comments.map((comment: any, i: number) => <div className="github-comment" key={i}><b>{comment.author}</b><div className="github-issue-body">{comment.body}</div>{comment.body_truncated && <small>Comment limited to 1,200 characters.</small>}</div>)}
          <p className="muted">Up to the first 10 comments are included. Saved evidence refreshes when you save again; it is not automatically synchronized.</p>
          {!canImport && <p className="muted">A workspace owner can save issue evidence.</p>}
        </>}
      </div>
    </div>}
  </section>;
}
