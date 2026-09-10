import { GithubOutlined } from "@ant-design/icons";

const GITHUB_URL = "https://github.com/Zuixi/stock-bot";

/** 宣传页页脚：免责声明 + 数据来源署名 + 版权 + GitHub */
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
      <div className="landing-container">
        <p className="landing-footer__sources">
          数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所
        </p>
      </div>
    </footer>
  );
}
