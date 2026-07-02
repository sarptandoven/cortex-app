import {
  App,
  Notice,
  Plugin,
  PluginSettingTab,
  requestUrl,
  Setting,
  TFile,
} from "obsidian";

interface CortexPluginSettings {
  endpoint: string;
  apiToken: string;
  autoSyncOnStartup: boolean;
  maxRecords: number;
}

interface CortexSyncResponse {
  status?: string;
  saved?: number;
  skipped?: number;
  failed?: number;
  queued?: number;
  records?: Array<{ status?: string }>;
  errors?: Array<{ error?: string; message?: string; reason?: string }>;
}

interface CortexReviewResponse {
  stats?: {
    pending_captures?: number;
    memories?: number;
  };
}

interface CortexActionBriefResponse {
  status?: string;
  markdown?: string;
  risk_flags?: Array<{ code?: string; message?: string }>;
  coverage?: {
    cited_memories?: number;
    unique_memories?: number;
  };
}

const DEFAULT_SETTINGS: CortexPluginSettings = {
  endpoint: "http://127.0.0.1:8766",
  apiToken: "",
  autoSyncOnStartup: false,
  maxRecords: 500,
};

const PLUGIN_NAME = "Cortex Memory";

export default class CortexMemoryPlugin extends Plugin {
  settings: CortexPluginSettings = DEFAULT_SETTINGS;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.addRibbonIcon("brain-circuit", "Sync vault to Cortex", () => {
      void this.syncVault({ showNotice: true });
    });

    this.addCommand({
      id: "sync-vault",
      name: "Sync this vault",
      callback: () => {
        void this.syncVault({ showNotice: true });
      },
    });

    this.addCommand({
      id: "check-review",
      name: "Check Review",
      callback: () => {
        void this.checkReview();
      },
    });

    this.addCommand({
      id: "prepare-action-brief",
      name: "Prepare action brief from current note",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) {
          return false;
        }
        if (!checking) {
          void this.prepareActionBrief(file);
        }
        return true;
      },
    });

    this.addSettingTab(new CortexSettingTab(this.app, this));

    if (this.settings.autoSyncOnStartup) {
      window.setTimeout(() => {
        void this.syncVault({ showNotice: false });
      }, 3000);
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = {
      ...DEFAULT_SETTINGS,
      ...((await this.loadData()) as Partial<CortexPluginSettings> | null),
    };
    this.settings.endpoint = normalizeEndpoint(this.settings.endpoint);
    this.settings.maxRecords = clampMaxRecords(this.settings.maxRecords);
  }

  async saveSettings(): Promise<void> {
    this.settings.endpoint = normalizeEndpoint(this.settings.endpoint);
    this.settings.maxRecords = clampMaxRecords(this.settings.maxRecords);
    await this.saveData(this.settings);
  }

  async syncVault({ showNotice }: { showNotice: boolean }): Promise<void> {
    const vaultPath = this.getVaultPath();
    if (!vaultPath) {
      new Notice("Cortex needs the desktop vault path. Open this vault in the Obsidian desktop app.");
      return;
    }

    if (showNotice) {
      new Notice("Cortex is syncing this vault...");
    }

    try {
      const payload = await this.cortexRequest<CortexSyncResponse>("/v1/connectors/obsidian/sync", {
        method: "POST",
        body: {
          vault_path: vaultPath,
          account_label: this.app.vault.getName(),
          account_identifier: this.app.vault.getName(),
          processing: "sync",
          max_records: this.settings.maxRecords,
          cursor_name: "obsidian-plugin",
        },
      });

      const saved = payload.saved ?? 0;
      const queued = payload.queued ?? 0;
      const failed = payload.failed ?? 0;
      const skipped = payload.skipped ?? 0;
      if (failed > 0) {
        new Notice(`Cortex synced with ${failed} issue${failed === 1 ? "" : "s"}. Open Cortex Review for details.`);
      } else {
        new Notice(`Cortex sync complete: ${saved} saved, ${queued} queued, ${skipped} unchanged.`);
      }
    } catch (error) {
      new Notice(`Cortex sync failed: ${errorMessage(error)}`);
    }
  }

  async checkReview(): Promise<void> {
    try {
      const payload = await this.cortexRequest<CortexReviewResponse>("/v1/review/today", {
        method: "GET",
      });
      const pending = payload.stats?.pending_captures ?? 0;
      const memories = payload.stats?.memories ?? 0;
      if (pending > 0) {
        new Notice(`Cortex Review has ${pending} item${pending === 1 ? "" : "s"} waiting.`);
      } else {
        new Notice(`Cortex Review is clear. ${memories} reviewed memor${memories === 1 ? "y" : "ies"} ready for Ask.`);
      }
    } catch (error) {
      new Notice(`Could not read Cortex Review: ${errorMessage(error)}`);
    }
  }

  async prepareActionBrief(file: TFile): Promise<void> {
    let noteText = "";
    try {
      noteText = await this.app.vault.cachedRead(file);
    } catch {
      noteText = "";
    }

    const task = actionBriefTask(file, noteText);
    try {
      const query = new URLSearchParams({ task, limit: "8", format: "json" });
      const payload = await this.cortexRequest<CortexActionBriefResponse>(`/v1/action-brief?${query.toString()}`, {
        method: "GET",
      });
      const markdown = payload.markdown?.trim();
      if (!markdown) {
        new Notice("Cortex did not return an action brief yet.");
        return;
      }
      await navigator.clipboard.writeText(markdown);
      const status = payload.status ? ` (${payload.status})` : "";
      const cited = payload.coverage?.cited_memories ?? 0;
      new Notice(`Copied Cortex action brief${status} with ${cited} cited memor${cited === 1 ? "y" : "ies"}.`);
    } catch (error) {
      new Notice(`Could not prepare Cortex action brief: ${errorMessage(error)}`);
    }
  }

  private getVaultPath(): string | null {
    const adapter = this.app.vault.adapter as unknown as { getBasePath?: () => string };
    const value = adapter.getBasePath?.();
    if (!value || !value.trim()) {
      return null;
    }
    return value;
  }

  private async cortexRequest<T>(
    path: string,
    options: { method: "GET" | "POST"; body?: Record<string, unknown> },
  ): Promise<T> {
    const endpoint = normalizeEndpoint(this.settings.endpoint);
    const url = `${endpoint}${path.startsWith("/") ? path : `/${path}`}`;
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    const token = this.settings.apiToken.trim();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }

    const response = await requestUrl({
      url,
      method: options.method,
      headers,
      contentType: options.body ? "application/json" : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
      throw: false,
    });

    if (response.status < 200 || response.status >= 300) {
      throw new Error(response.text || `HTTP ${response.status}`);
    }

    return response.json as T;
  }
}

class CortexSettingTab extends PluginSettingTab {
  plugin: CortexMemoryPlugin;

  constructor(app: App, plugin: CortexMemoryPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: PLUGIN_NAME });
    containerEl.createEl("p", {
      text: "Connect this vault to the local Cortex app. Cortex keeps extraction, review, retrieval, and AI access controls in Cortex.",
    });

    new Setting(containerEl)
      .setName("Cortex endpoint")
      .setDesc("Local Cortex app endpoint.")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.endpoint)
          .setValue(this.plugin.settings.endpoint)
          .onChange(async (value) => {
            this.plugin.settings.endpoint = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("API token")
      .setDesc("Required when Cortex local auth is enabled. The token is stored in this vault's plugin settings.")
      .addText((text) => {
        text.inputEl.type = "password";
        text
          .setPlaceholder("Cortex API token")
          .setValue(this.plugin.settings.apiToken)
          .onChange(async (value) => {
            this.plugin.settings.apiToken = value.trim();
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName("Auto-sync on startup")
      .setDesc("Run Cortex sync a few seconds after Obsidian opens.")
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoSyncOnStartup)
          .onChange(async (value) => {
            this.plugin.settings.autoSyncOnStartup = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Max records per sync")
      .setDesc("Caps how many note sections Cortex scans per sync.")
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.maxRecords))
          .setValue(String(this.plugin.settings.maxRecords))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.plugin.settings.maxRecords = clampMaxRecords(Number.isFinite(parsed) ? parsed : DEFAULT_SETTINGS.maxRecords);
            await this.plugin.saveSettings();
          }),
      );
  }
}

function normalizeEndpoint(value: string): string {
  const trimmed = (value || DEFAULT_SETTINGS.endpoint).trim().replace(/\/+$/, "");
  return trimmed || DEFAULT_SETTINGS.endpoint;
}

function clampMaxRecords(value: number): number {
  return Math.max(1, Math.min(5000, Math.round(value || DEFAULT_SETTINGS.maxRecords)));
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error || "Unknown error");
}

function actionBriefTask(file: TFile, noteText: string): string {
  const title = file.basename.trim();
  const firstUsefulLine =
    noteText
      .split(/\r?\n/)
      .map((line) => line.replace(/^#+\s*/, "").trim())
      .find((line) => line.length > 12 && !line.startsWith("---")) ?? "";
  const basis = [title, firstUsefulLine].filter(Boolean).join(": ");
  return basis ? `Use Cortex memory for ${basis}` : `Use Cortex memory for ${title || "the current note"}`;
}
