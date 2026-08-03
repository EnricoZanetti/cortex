import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "./nav";

export const metadata: Metadata = {
  title: "Document Intelligence: Knowledge Base",
  description:
    "Ask questions in natural language, and manage the documents the assistant searches.",
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
          <div>
            <h1>Document Intelligence</h1>
            <p>
              Ask questions in plain language. Answers are grounded in the uploaded
              documents and retrieved through the MCP server.
            </p>
          </div>
          <Nav />
        </header>
        <main className="container">{children}</main>
        <footer className="footer">Built by Enrico Zanetti</footer>
      </body>
    </html>
  );
}
