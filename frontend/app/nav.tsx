"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "../lib/auth-context";

const TABS = [
  { href: "/chat", label: "Ask" },
  { href: "/documents", label: "Documents" },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout, loading } = useAuth();

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
      {!loading && user && (
        <Link
          href="/settings"
          className={pathname === "/settings" ? "nav-tab active" : "nav-tab"}
        >
          Settings
        </Link>
      )}
      {!loading && user && (
        <button
          type="button"
          className="link neutral"
          onClick={() => {
            logout();
            router.push("/login");
          }}
        >
          Log out ({user.username})
        </button>
      )}
      {!loading && !user && (
        <Link href="/login" className={pathname === "/login" ? "nav-tab active" : "nav-tab"}>
          Log in
        </Link>
      )}
    </nav>
  );
}
