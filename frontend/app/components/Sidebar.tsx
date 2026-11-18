'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const Icon = ({ d, circle }: { d: string; circle?: boolean }) => (
  <svg className="nav-ico" width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {circle && <circle cx="12" cy="12" r="9" />}
    <path d={d} />
  </svg>
);

const ITEMS = [
  { href: '/chat', label: 'Chat', icon: <Icon d="M4 5h16v11H9l-5 4z" /> },
  { href: '/campaigns', label: 'Campaigns', icon: <Icon d="M4 20V10 M10 20V4 M16 20v-6 M2 20h20" /> },
  { href: '/review', label: 'Review queue', icon: <Icon circle d="M12 7v5l3 2" /> },
  { href: '/settings', label: 'Settings', icon: <Icon d="M4 7h16 M4 12h16 M4 17h16 M9 5v4 M15 10v4 M7 15v4" /> },
];

export default function Sidebar() {
  const path = usePathname();
  return (
    <aside className="sidebar">
      <div className="brand">Campaign Reply Agent<small>Local copy, runs on your machine</small></div>
      {ITEMS.map((it) => (
        <Link key={it.href} href={it.href} aria-label={it.label}
          className={'nav-item' + (path.startsWith(it.href) ? ' active' : '')}>
          {it.icon}
          <span className="nav-lbl">{it.label}</span>
        </Link>
      ))}
    </aside>
  );
}
