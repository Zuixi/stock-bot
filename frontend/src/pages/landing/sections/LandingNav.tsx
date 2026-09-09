import { useEffect, useState } from "react";
import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { ThemeToggle } from "@/shared/ui";
import { CtaButton } from "./CtaButton";

const NAV_ANCHORS = [
  { href: "#product", label: "功能" },
  { href: "#data", label: "数据" },
  { href: "#industry", label: "行业" },
];

/** 宣传页导航：透明底，滚动 >24px 后加 var(--bg-page) 与底边框 */
export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const navigate = useNavigate();

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
        <nav className="landing-nav-links" aria-label="宣传页锚点导航">
          {NAV_ANCHORS.map((a) => (
            <a key={a.href} className="landing-nav-link" href={a.href}>
              {a.label}
            </a>
          ))}
        </nav>
        <div className="landing-nav-actions">
          <ThemeToggle />
          <Button type="text" onClick={() => navigate("/login")}>
            登录
          </Button>
          <CtaButton testId="landing-cta" />
        </div>
      </div>
    </header>
  );
}
