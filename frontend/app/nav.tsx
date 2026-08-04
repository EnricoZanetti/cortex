"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/chat", label: "Ask" },
  { href: "/documents", label: "Documents" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav">
      {TABS.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          className={pathname === tab.href ? "nav-tab active" : "nav-tab"}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
