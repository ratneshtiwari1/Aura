import { useLayoutEffect, useRef, useState } from 'react';

const sources = [
  { name: 'Power BI semantic models', badge: '●', color: '#e77b00' },
  { name: 'Dataflows', badge: '◆', color: '#555' },
  { name: 'Dataverse', badge: '◉', color: '#555' },
  { name: 'Analysis Services', badge: '◆', color: '#a43bb0' },
  { name: 'Text/CSV', badge: '≡', color: '#555' },
  { name: 'OData feed', badge: '⊞', color: '#ed7900' },
];

export function MoreDataSources({ disabled }: { disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    const trigger = triggerRef.current;
    if (!menu || !trigger) return;
    const position = () => {
      const anchor = trigger.getBoundingClientRect();
      const bounds = menu.getBoundingClientRect();
      const left = Math.max(8, Math.min(anchor.left, window.innerWidth - bounds.width - 8));
      const below = anchor.bottom + 6;
      const top = below + bounds.height <= window.innerHeight - 8
        ? below
        : Math.max(8, Math.min(anchor.top - bounds.height - 6, window.innerHeight - bounds.height - 8));
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };
    position();
    menu.querySelector<HTMLButtonElement>('button')?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.contains(event.target) && !trigger.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        trigger.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open]);

  return <>
    <button ref={triggerRef} type="button" className="home-secondary-button" disabled={disabled} aria-expanded={open} onClick={() => setOpen((current) => !current)}>More</button>
    {open && <div ref={menuRef} className="common-sources-menu" role="group" aria-label="Other data sources">
      <h2>Other data sources</h2>
      {sources.map((source) => {
        return <button key={source.name} type="button" className={selected === source.name ? 'is-selected' : undefined}
          aria-label={`${source.name} (demo only)`}
          aria-pressed={selected === source.name}
          onClick={() => setSelected(source.name)}>
          <span className="common-source-icon" aria-hidden="true">
            <svg viewBox="0 0 24 28"><path d="M3 1h12l6 6v20H3z" /><path d="M15 1v6h6" /></svg>
            <span style={{ backgroundColor: source.color }}>{source.badge}</span>
          </span>
          {source.name}
        </button>;
      })}
    </div>}
  </>;
}
