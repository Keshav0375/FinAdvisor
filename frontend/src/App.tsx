import { ChatWindow } from "@/components/ChatWindow";
import { Header } from "@/components/Header";

export default function App() {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl">
        <ChatWindow />
      </main>
    </>
  );
}
