import './globals.css';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';

export const metadata = { title: 'Campaign Reply Agent' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" dir="ltr">
      <body>
        <div className="shell">
          <Sidebar />
          <div className="main">
            <TopBar />
            <div className="content">{children}</div>
          </div>
        </div>
      </body>
    </html>
  );
}
