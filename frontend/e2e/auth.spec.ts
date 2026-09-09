import { test, expect as baseExpect } from "@playwright/test";

/**
 * 认证与会话管理前端 E2E 测试套件。
 * - 未登录重定向（路由守卫 RequireAuth 拦截与 returnTo 保持）
 * - 登录/注册表单交互与客户端校验
 * - 鉴权成功后 Header 用户状态（用户名、角色徽章）展示与跳转
 * - 登出确认弹窗与状态清理重定向
 */

const expect = baseExpect.configure({ timeout: 15_000 });

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
    await expect(page.getByText("用户登录")).toBeVisible();

    // 2. 尝试访问自定义标签页
    await page.goto("/tags");
    await page.waitForURL(/\/login\?returnTo=%2Ftags/);
    await expect(page.getByText("用户登录")).toBeVisible();
  });

  test("登录与注册表单交互：Tab 切换与表单基础校验", async ({ page }) => {
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

    // 切换至注册 Tab
    await page.getByRole("tab", { name: "新用户注册" }).click();
    await expect(page.getByPlaceholder("用户名 (英文字符、数字)")).toBeVisible();
    await expect(page.getByPlaceholder("电子邮箱")).toBeVisible();
    const registerButton = page.getByRole("button", { name: "立即注册" });
    await expect(registerButton).toBeVisible();

    // 触发注册表单校验
    // 注：AntD Tabs 隐藏面板仍挂载，登录 Tab 的「请输入用户名或邮箱」也在 DOM 中，
    // 子串「请输入用户名」会双匹配——用精确匹配限定注册表单的提示
    await registerButton.click();
    await expect(page.getByText("请输入用户名", { exact: true })).toBeVisible();
    await expect(page.getByText("请输入邮箱地址")).toBeVisible();
  });

  test("鉴权登录成功：Header 展示用户信息与角色 Tag，并正确处理登出流程", async ({ page }) => {
    let sessionActive = false;

    const mockUser = {
      id: "usr-e2e-001",
      username: "trader_alice",
      email: "alice@example.com",
      display_name: "爱丽丝",
      status: "active",
      is_superuser: false,
      roles: ["trader"],
      permissions: ["stocks:read", "watchlist:manage", "tags:manage"],
      created_at: "2026-09-09T00:00:00Z",
    };

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
