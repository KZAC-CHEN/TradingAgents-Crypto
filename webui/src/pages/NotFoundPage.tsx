import { Link } from "react-router-dom";

export function NotFoundPage() {
  return <div className="empty-state full-page"><h1>页面不存在</h1><p>请求的页面可能已移动。</p><Link className="button button-primary" to="/">返回控制台</Link></div>;
}
