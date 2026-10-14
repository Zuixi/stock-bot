import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * 账号设置（自助改资料）走真实栈：
 * 登录 → 用户菜单 → 账号设置 → 改显示名 → Header 立即更新 → 改回原值。
 * 凭据经环境变量注入；缺失则整组跳过（与 research/admin-users 同约定）。
 *
 * 用例自身保持幂等：读完原值再改，最后还原（含把空的 display_name 还原成空）。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const ADMIN_USER = process.env.E2E_ADMIN_USER;
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD;

async function readProfile(page: Page) {
  return await page.evaluate(async () => {
    const r = await fetch("/auth/session", { credentials: "include" });
    return (await r.json()) as { display_name: string | null; username: string };
  });
}

async function saveDisplayName(page: Page, value: string) {
  await page.locator("header .ant-avatar").click();
  await page.getByText("账号设置").click();
  const dialog = page.getByRole("dialog").filter({ hasText: "账号设置" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("显示名").fill(value);
  await dialog.getByRole("button", { name: /保\s*存/ }).click();
  await expect(dialog).toBeHidden();
}

test.describe("账号设置（自助改资料）", () => {
  test.skip(!ADMIN_USER || !ADMIN_PASSWORD, "需要 E2E_ADMIN_USER / E2E_ADMIN_PASSWORD");

  test("用户可改自己的显示名，Header 立即生效并可还原", async ({ page }) => {
    // ── 登录（走真实登录表单）──
    await page.goto("/login?returnTo=%2Fmarket");
    await page.getByPlaceholder("用户名 / 邮箱").fill(ADMIN_USER!);
    await page.getByPlaceholder("密码", { exact: true }).fill(ADMIN_PASSWORD!);
    await page.getByRole("button", { name: "登 录" }).click();
    await page.waitForURL(/\/market$/);

    const original = await readProfile(page);
    const newName = `E2E改名${Date.now().toString(36)}`;

    try {
      await saveDisplayName(page, newName);
      // Header 立即显示新显示名
      await expect(page.locator("header").getByText(newName, { exact: true })).toBeVisible();

      // 服务端确实落库
      await expect
        .poll(async () => (await readProfile(page)).display_name, { timeout: 10_000 })
        .toBe(newName);
    } finally {
      // 还原（display_name 为空字符串时后端会存回 NULL）
      await saveDisplayName(page, original.display_name ?? "");
      await expect
        .poll(async () => (await readProfile(page)).display_name, { timeout: 10_000 })
        .toBe(original.display_name);
    }
  });
});
