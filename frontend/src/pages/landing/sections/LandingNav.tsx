import { useEffect, useState } from "react";
import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { ThemeToggle } from "@/shared/ui";
import { useAuthStore } from "@/features/auth/store";
import { UserMenu } from "@/features/auth/components/UserMenu";
import { CtaButton } from "./CtaButton";

/** 行情为主：导航锚点指向首页五大行情区块（calendar 无导航入口，Phase 4 另立） */
const NAV_ANCHORS = [
  { id: "pulse", label: "脉搏" },
  { id: "rankings", label: "榜单" },
  { id: "sectors", label: "板块" },
  { id: "money", label: "资金" },
  { id: "news", label: "快讯" },
] as const;

/** 宣传页导航：透明底，滚动 >24px 后加 var(--bg-page) 与底边框 */
export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isAuthReady = useAuthStore((s) => s.isAuthReady);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className={scrolled ? "landing-nav landing-nav--scrolled" : "landing-nav"}>
      <div className="landing-nav-inner">
        <a className="landing-nav-logo" href="#hero" aria-label="StockBot 首页">
          StockBot
        </a>
        <nav className="landing-nav-links" aria-label="行情台区块导航">
          {NAV_ANCHORS.map((a) => (
            <a key={a.id} className="landing-nav-link" href={`#${a.id}`}>
              {a.label}
            </a>
          ))}
        </nav>
        <div className="landing-nav-actions">
          <ThemeToggle />
          {/* 会话探测完成前不渲染互斥入口，避免「登录」闪现；已登录交给 UserMenu，
              与 CtaButton 的「进入工作台」保持一致，不再同时给出两个互斥动作。 */}
          {isAuthReady &&
            (isAuthenticated ? (
              <UserMenu />
            ) : (
              <Button type="text" onClick={() => navigate("/login")}>
                登录
              </Button>
            ))}
          <CtaButton testId="landing-cta" />
        </div>
      </div>
    </header>
  );
}
