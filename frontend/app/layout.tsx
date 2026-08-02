import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Document Intelligence — Knowledge Base",
  description: "Upload, tag and manage the documents exposed to AI agents via MCP.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="header">
          <h1>Document Intelligence</h1>
          <p>
            Documents uploaded here are chunked, embedded and exposed to AI agents
            through the MCP server.
          </p>
        </header>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
