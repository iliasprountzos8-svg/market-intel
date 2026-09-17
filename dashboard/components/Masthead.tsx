"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/markets", label: "Markets" },
  { href: "/news", label: "News" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/track-record", label: "Track Record" },
  { href: "/archive", label: "Archive" },
  { href: "/admin", label: "Admin" },
  { href: "/digest-editor", label: "Write Digest" },
  { href: "/settings", label: "Settings" },
];

export default function Masthead() {
  const pathname = usePathname();
  const today = new Date().toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" });

  return (
    <div className="masthead-wrap">
      <div className="masthead-top">
        <span className="masthead-date">{today}</span>
        <span className="masthead-live"><span className="live-dot" />Live</span>
      </div>
      <Link href="/" className="masthead-title">Market Intel</Link>
      <div className="masthead-sub">AI-Read Market Intelligence &amp; Portfolio Signal</div>
      <nav className="masthead-nav">
        {NAV.map(({ href, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link key={href} href={href} className={`masthead-link ${active ? "active" : ""}`}>
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
