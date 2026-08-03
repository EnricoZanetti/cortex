import { redirect } from "next/navigation";

/** The chat is the primary experience; the root sends people there. */
export default function Home() {
  redirect("/chat");
}
