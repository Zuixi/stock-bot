import { GithubOutlined } from "@ant-design/icons";

const GITHUB_URL = "https://github.com/Zuixi/stock-bot";

/** 宣传页页脚：免责声明 + 版权 + GitHub */
export function LandingFooter() {
  return (
    <footer className="landing-footer">
      <div className="landing-container landing-footer-inner">
        <span>本站数据仅供参考，不构成投资建议</span>
        <span>© {new Date().getFullYear()} StockBot</span>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">
          <GithubOutlined />
          GitHub
        </a>
      </div>
    </footer>
  );
}
