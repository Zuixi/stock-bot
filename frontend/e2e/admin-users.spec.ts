import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * 邀请制开户全链路（ADR 0010，走真实 gateway/auth-service，不打 mock）：
 * admin 开户 → 改角色（UI）→ 拿到一次性临时密码 → 新用户首登被强制改密 → 进入工作台。
 * 凭据经环境变量注入（不落盘）；缺失则整组跳过（与 research.spec 同约定）。
 *
 * 注意网关对 /auth/* 挂了 auth-ratelimit（20/s，burst 10，防爆破的正确配置）。
 * 本 spec 的 /auth 往返比别的 spec 多，且会与并发 spec 共享同一 IP 额度，
 * 因此所有调用统一走 429 退避重试——绝不放宽生产限流来迁就测试。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;

const RETRY_DELAYS_MS = [500, 1000, 2000];

/** fetch inside the page, retrying on 429 (rate limiter) with backoff. */
async function api(
  page: Page,
  url: string,
  init: { method?: string; body?: unknown; csrf?: string } = {}
): Promise<{ status: number; body: any }> {
  return await page.evaluate(
    async ({ url, method, body, csrf, delays }) => {
      for (let attempt = 0; ; attempt++) {
        const resp = await fetch(url, {
          method: method ?? "GET",
          headers: {
            ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
            ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          },
          credentials: "include",
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        const text = await resp.text();
        if (resp.status !== 429 || attempt >= delays.length) {
          return { status: resp.status, body: text ? JSON.parse(text) : null };
        }
        await new Promise((r) => setTimeout(r, delays[attempt]));
      }
    },
    { url, method: init.method, body: init.body, csrf: init.csrf, delays: RETRY_DELAYS_MS }
  );
}

async function csrf(page: Page): Promise<string> {
  return (await api(page, "/auth/csrf")).body.csrf_token as string;
}

/**
 * 打开控制台并确认它真的渲染了。
 *
 * 应用自身的水合请求（/auth/session）不受本 spec 控制，它也会吃 /auth 限流的
 * 429；而 AuthProvider 对所有错误一律 catch 成"未登录"（既有行为），于是页面
 * 表现为 403 页。这里按"限流是暂时的"做有限重试——不放宽限流，也不掩盖真失败。
 */
async function gotoAdminUsers(page: Page): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    await page.goto("/admin/users");
    try {
      await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible({
        timeout: 5_000,
      });
      return;
    } catch (err) {
      if (attempt >= 2) throw err;
      await page.waitForTimeout(2_000); // 等 /auth 限流额度回落
    }
  }
}

test.describe("管理员控制台与邀请制开户", () => {
  test.skip(!ADMIN_USER || !ADMIN_PASSWORD, "需要 E2E_ADMIN_USER / E2E_ADMIN_PASSWORD");

  test("admin 开户 → 改角色 → 临时密码 → 强制改密 → 进入工作台", async ({ page }) => {
    await page.goto("/login");

    // ── 1. admin 登录（匿名 double-submit → 会话绑定 CSRF）──
    const anonCsrf = await csrf(page);
    expect((await api(page, "/auth/login", {
      method: "POST",
      body: { username_or_email: ADMIN_USER, password: ADMIN_PASSWORD },
      csrf: anonCsrf,
    })).status).toBe(200);
    const sessionCsrf = await csrf(page);

    // ── 2. 控制台可见并能开户 ──
    await gotoAdminUsers(page);
    await expect(page.getByRole("button", { name: "开 户" })).toBeVisible();

    const suffix = Date.now().toString(36);
    const username = `e2e_invite_${suffix}`;
    const prov = await api(page, "/auth/admin/users", {
      method: "POST",
      csrf: sessionCsrf,
      body: {
        username,
        email: `${username}@example.com`,
        display_name: "E2E 受邀用户",
        roles: ["analyst"],
      },
    });
    expect(prov.status).toBe(201);
    const tempPassword = prov.body.initial_password as string;
    const userId = prov.body.user.id as string;
    expect(tempPassword.length).toBeGreaterThanOrEqual(8);

    await gotoAdminUsers(page);
    await expect(page.getByText(username)).toBeVisible();

    // ── 3. 改角色走 UI：这是「角色分配不再改 SQL」的落点 ──
    await page.getByText(username).first().click();
    await page.getByText("改角色").click();
    const roleDialog = page.getByRole("dialog").filter({ hasText: `修改 ${username} 的角色` });
    await expect(roleDialog).toBeVisible();
    // 作用域收窄：开户弹窗的 Select 仍挂在 DOM 里，全局选择器会撞 strict mode
    await roleDialog.locator(".ant-select-selector").click();
    await page
      .locator(".ant-select-dropdown:visible")
      .getByTitle("专业投研员（researcher）")
      .click();
    // AntD 多选选中后下拉不自动收起，浮层会挡住保存按钮：点标题失焦先收下拉
    await roleDialog.getByText(`修改 ${username} 的角色`).click();
    await expect(page.locator(".ant-select-dropdown:visible")).toHaveCount(0);
    await page.getByRole("button", { name: "保存角色" }).click();

    await expect
      .poll(
        async () => ((await api(page, `/auth/admin/users/${userId}`)).body.roles as string[]).sort().join(","),
        { timeout: 15_000, intervals: [1000, 2000] }
      )
      .toBe("analyst,researcher");

    // ── 4. admin 退出；新用户用临时密码登录 ──
    const logoutCsrf = await csrf(page);
    await api(page, "/auth/logout", { method: "POST", csrf: logoutCsrf });
    expect((await api(page, "/auth/login", {
      method: "POST",
      body: { username_or_email: username, password: tempPassword },
      csrf: await csrf(page),
    })).status).toBe(200);

    // ── 5. 强制改密：/api/* 被 403 门控，前端整页跳到 /change-password ──
    // 门控用 window.location.assign 整页跳转，会中断 /market 的加载
    // （ERR_ABORTED）——这是预期行为，不是缺陷。
    await page.goto("/market").catch(() => {});
    await page.waitForURL(/\/change-password/);
    expect((await api(page, "/api/v1/watchlists")).status).toBe(403);

    // ── 6. 完成改密后门控解除 ──
    const newPassword = `E2eOwn${suffix}Pass!`;
    await page.getByLabel("当前（临时）密码").fill(tempPassword);
    await page.getByLabel("新密码", { exact: true }).fill(newPassword);
    await page.getByLabel("确认新密码").fill(newPassword);
    await page.getByRole("button", { name: "完成修改并继续" }).click();

    await page.waitForURL((url) => !url.pathname.includes("/change-password"), {
      timeout: 15_000,
    });
    await expect
      .poll(async () => (await api(page, "/api/v1/watchlists")).status, {
        timeout: 15_000,
        intervals: [1000, 2000],
      })
      .toBe(200);
  });
});
