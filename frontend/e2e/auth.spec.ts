import { test, expect as baseExpect } from "@playwright/test";

/**
 * 认证与会话管理前端 E2E 测试套件。
 * - 未登录重定向（路由守卫 RequireAuth 拦截与 returnTo 保持）
 * - 登录/注册表单交互与客户端校验
 * - 鉴权成功后 Header 用户状态（用户名、角色徽章）展示与跳转
 * - 登出确认弹窗与状态清理重定向
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** 会话 mock 的单一来源：形状必须与 UserProfile 契约同源，否则各用例各写一份会漂移 */
const MOCK_USER = {
  id: "usr-e2e-001",
  username: "trader_alice",
  email: "alice@example.com",
  display_name: "爱丽丝",
  status: "active",
  is_superuser: false,
  must_change_password: false,
  roles: ["trader"],
  permissions: ["stocks:read", "watchlists:write", "tags:write"],
  created_at: "2026-09-09T00:00:00Z",
};

test.describe("认证微服务与前端会话状态机", () => {
  test("未登录状态下访问受保护路由被重定向至登录页并携带 returnTo 参数", async ({ page }) => {
    // 模拟会话接口返回 401 未登录
    await page.route("**/auth/session", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          code: "AUTH_UNAUTHORIZED",
          message: "未登录或会话已过期",
        }),
      })
    );

    // 1. 尝试访问自选股页面
    await page.goto("/watchlist");
    await page.waitForURL(/\/login\?returnTo=%2Fwatchlist/);
    await expect(page.getByRole("button", { name: "登 录" })).toBeVisible();

    // 2. 尝试访问自定义标签页
    await page.goto("/tags");
    await page.waitForURL(/\/login\?returnTo=%2Ftags/);
    await expect(page.getByRole("button", { name: "登 录" })).toBeVisible();

    // 3. 尝试访问投研板块（已需登录才可阅读，见 docs/decisions/0008）
    await page.goto("/research");
    await page.waitForURL(/\/login\?returnTo=%2Fresearch/);
    await expect(page.getByRole("button", { name: "登 录" })).toBeVisible();
  });

  test("邀请制：登录页无注册入口，含邀请制说明", async ({ page }) => {
    await page.goto("/login");

    // 验证登录表单要素
    const usernameInput = page.getByPlaceholder("用户名 / 邮箱");
    const passwordInput = page.getByPlaceholder("密码", { exact: true });
    const loginButton = page.getByRole("button", { name: "登 录" });

    await expect(usernameInput).toBeVisible();
    await expect(passwordInput).toBeVisible();
    await expect(loginButton).toBeVisible();

    // 触发空表单校验
    await loginButton.click();
    await expect(page.getByText("请输入用户名或邮箱")).toBeVisible();
    await expect(page.getByText("请输入密码")).toBeVisible();

    // ADR 0010：注册入口必须不存在（无 Tab、无按钮、无表单）
    await expect(page.getByRole("tab")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "立即注册" })).toHaveCount(0);
    await expect(page.getByText("本系统采用邀请制，账号由管理员开通")).toBeVisible();
  });

  test("鉴权登录成功：Header 展示用户信息与角色 Tag，并正确处理登出流程", async ({ page }) => {
    let sessionActive = false;
    const mockUser = MOCK_USER;

    // 动态拦截会话接口
    await page.route("**/auth/session", (route) => {
      if (sessionActive) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockUser),
        });
      }
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "未登录" }),
      });
    });

    await page.route("**/auth/csrf", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ csrf_token: "csrf-mock-token-xyz" }),
      })
    );

    await page.route("**/auth/login", (route) => {
      sessionActive = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: mockUser,
          session_id: "sess-e2e-123",
          csrf_token: "csrf-mock-token-xyz",
          expires_in: 86400,
        }),
      });
    });

    await page.route("**/auth/logout", (route) => {
      sessionActive = false;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ message: "登出成功" }),
      });
    });

    // 访问登录页并带 returnTo
    await page.goto("/login?returnTo=%2Fmarket");

    // 填写凭证并提交
    await page.getByPlaceholder("用户名 / 邮箱").fill("trader_alice");
    await page.getByPlaceholder("密码", { exact: true }).fill("SecurePassword123!");
    await page.getByRole("button", { name: "登 录" }).click();

    // 校验登录成功后跳转至 /market
    await page.waitForURL(/\/market$/);

    // 校验 Header 中展示登录昵称或用户名
    const userMenuTrigger = page.getByText("爱丽丝");
    await expect(userMenuTrigger).toBeVisible();
    await expect(page.getByRole("button", { name: "登录" })).toHaveCount(0);

    // 点击用户菜单唤出 Dropdown
    await userMenuTrigger.click();
    await expect(page.getByText("alice@example.com")).toBeVisible();
    await expect(page.getByText("trader", { exact: true })).toBeVisible();

    // 点击退出登录项
    const logoutMenuItem = page.getByText("退出登录");
    await expect(logoutMenuItem).toBeVisible();
    await logoutMenuItem.click();

    // 校验 Ant Design 确认弹窗（自定义 modal-title 处于 hidden 态，锚定 confirm-title）
    const confirmModal = page.locator(".ant-modal");
    await expect(confirmModal).toBeVisible();
    await expect(confirmModal.locator(".ant-modal-confirm-title")).toHaveText("确认退出登录");

    // 点击确认退出
    const confirmOkButton = confirmModal.getByRole("button", { name: "退 出" });
    await confirmOkButton.click();

    // 验证登出后页面重定向回 /login，Header 恢复为"登录"按钮
    await page.waitForURL(/\/login$/);
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
  });
});

  test("会话探测遇暂时性故障：重试后自愈，不被误判为未登录", async ({ page }) => {
    // 首次 503（模拟限流/上游抖动），之后正常 —— 这正是"刷新一下就被踢到
    // 登录页"的真实触发条件。
    let probeCalls = 0;
    await page.route("**/auth/session", (route) => {
      probeCalls += 1;
      if (probeCalls === 1) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ code: "UPSTREAM_UNAVAILABLE", message: "认证服务暂不可用" }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_USER),
      });
    });
    await page.route("**/api/v1/watchlists", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) })
    );

    await page.goto("/watchlist");

    // 关键：以已登录身份停在被保护的页面上，而不是被重定向到 /login
    await expect(page.getByText("爱丽丝")).toBeVisible();
    await expect(page).toHaveURL(/\/watchlist/);
    expect(probeCalls).toBeGreaterThan(1); // 确实走了重试，不是碰巧第一次就成功
  });

  test("会话探测持续失败：给可重试提示而非跳登录页，恢复后放行", async ({ page }) => {
    let failing = true;
    await page.route("**/auth/session", (route) =>
      failing
        ? route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ code: "UPSTREAM_UNAVAILABLE", message: "认证服务暂不可用" }),
          })
        : route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(MOCK_USER),
          })
    );
    await page.route("**/api/v1/watchlists", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) })
    );

    await page.goto("/watchlist");

    // 登录态未知：不跳登录页（跳过去同样连不上后端，且会让用户以为被踢下线）
    await expect(page.getByText("无法确认登录状态")).toBeVisible();
    await expect(page).not.toHaveURL(/\/login/);

    // 上游恢复后点重试 → 放行
    failing = false;
    // AntD 会给双汉字按钮插空格，可访问名是"重 试"
    await page.getByRole("button", { name: /重\s*试/ }).click();
    await expect(page.getByText("爱丽丝")).toBeVisible();
  });
