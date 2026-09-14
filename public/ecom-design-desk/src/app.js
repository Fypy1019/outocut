// 电商AI图片提示词生成器 - Vue 3 主应用（三栏AI聊天风格）
const { createApp, ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } = Vue;

// ============ 会话工厂 ============
function createSession() {
  return {
    id: 's_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
    name: formatSessionTime(new Date()),
    createdAt: new Date().toISOString(),
    messages: [],
    stepData: {
      formData: {
        platform: '', imageType: '', productName: '', category: '',
        specs: '', sellingPoints: '', targetAudience: '', usageScene: '',
        priceRange: '', brandName: '', brandElements: '',
        avoidContent: '', productVersion: '', visualNotes: ''
      },
      productImage: null,
      competitorSearchContext: null,
      competitorResearch: null,
      variableSuggestion: null,
      creativeConfirmation: {
        pageStrategy: 'minimal-selling-points',
        style: '', mainImageCount: 5, detailScreenCount: 10, contentRhythm: ''
      },
      differentiationStrategy: null,
      platformSizeAdvice: null,
      finalPrompts: null,
      mainImagePrompts: [],
      detailPrompts: []
    },
    currentStep: 0,
    completedSteps: []
  };
}

function formatSessionTime(d) {
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STORAGE_KEY = 'ecom-prompt-gen-sessions';
const BUILTIN_EXAMPLE_VERSION_KEY = 'ecom-prompt-gen-example-2026-09-06-v1';

const PLATFORM_NAMES = {
  taobao: '淘宝', tmall: '天猫', jd: '京东', pdd: '拼多多',
  douyin: '抖音电商', xiaohongshu: '小红书', '1688': '1688', amazon: '亚马逊'
};

const STEPS = [
  { label: '固定输入', icon: '📋' },
  { label: '竞品研究', icon: '🔍' },
  { label: '变量推荐', icon: '🎯' },
  { label: '差异化策略', icon: '💡' },
  { label: '平台尺寸', icon: '📐' },
  { label: '提示词生成', icon: '✨' }
];

const PAGE_STRATEGY_NAMES = {
  'minimal-selling-points': '极简卖点拆页',
  'minimal-effect-story': '极简效果拆页（弱化包装）',
  auto: 'AI自动编排'
};

const RETRY_ERROR_PREFIXES = [
  ['❌ 竞品研究失败：', 'research'],
  ['❌ 变量推荐失败：', 'variables'],
  ['❌ 差异化策略生成失败：', 'strategy'],
  ['❌ 提示词生成失败：', 'prompts']
];

function pageStrategyRules(confirmation) {
  if (confirmation?.pageStrategy === 'minimal-effect-story') {
    return `
## 页面编排策略：极简效果拆页（必须执行）
- 主图1是唯一固定以完整产品包装为视觉主体的页面，负责产品识别、规格/数量和最强购买理由。
- 主图2起默认不展示产品包装，不得为了露出产品而机械摆放包装。每张只表达一个不同核心卖点，以擦拭前后效果、真实使用动作、适用场景、材质微距、液体/去污表现、数据图形或合规证据作为唯一视觉中心。
- 只有当本页必须说明开盖方式、包装结构、规格数量、便携设计或使用步骤，离开产品就无法讲清时，才允许再次展示包装；即使出现，也只能作为辅助元素，不能抢占卖点效果主体。
- 详情页第1屏可以展示完整产品与规格，第2屏为卖点目录；此后默认采用效果、场景和证据画面，不重复陈列包装。最后的规格或使用方法页面可按需再次出现产品。
- 示例：“擦后更整洁”只展示擦拭动作或擦前/擦后效果；“高含水量”只展示湿润材质、水滴或液体效果；“柔厚耐用”只展示拉伸和材质微距；这些页面不要出现产品包装。
- 每张图只允许1个主标题，以及可选的1个辅助说明或1组数据/证明；合计最多2个文字区块。主标题建议2—6个汉字，辅助说明不超过10个汉字。
- 单张只保留1个主体和最多1个辅助视觉元素；禁止同时堆叠多个人物、产品、水花、图标、标签和装饰框。
- 画面约35%—55%保持为有完整背景质感的有意图负空间，主体不铺满画布；采用大字号无衬线中文和统一的单一编号/色块组件建立系列感。
- 背景优先使用纯色、轻微渐变、柔光或单一材质，不使用复杂布景、密集图标、成组标签和大段说明文字。
- 产品图仅作为产品身份和必要外观参考，不代表每张图都必须把产品或包装画进画面。
- 只有用户明确提供的产品信息、检测或参数可作为功效、成分、安全和数字宣称依据；不得编造。`;
  }
  if (confirmation?.pageStrategy !== 'minimal-selling-points') return '';
  return `
## 页面编排策略：极简卖点拆页（必须执行）
- 所有主图都承担卖点表达任务，但禁止把全部卖点堆入每一张图。
- 主图1为产品总览：产品主体、规格/数量、最强购买理由；主图2起，每张只表达一个不同的核心卖点，并用场景、产品特写、材质、数据或合规证据之一进行证明。
- 详情页第1屏为产品与规格总览；第2屏为核心卖点目录；后续页面按卖点优先级逐个展开。屏数充足时，一个重要卖点使用“结论页→证据/场景页”成对展开；最后用适用范围、使用方法或规格总结收口。
- 每张图只允许1个主标题、1个辅助说明、最多1组数据/证明，合计不超过3个文字区块；主标题建议4—8个汉字，辅助说明不超过14个汉字。
- 采用单一视觉中心、大字号无衬线中文、清晰色块/线条/编号组件和有意图的负空间；负空间必须服务于聚焦和版式平衡，不是待编辑区域。
- 只有产品信息或用户提供的检测/参数能够作为功效、成分、安全和数字宣称依据；资料未提供时不得编造。
- 卖点不足时可拆分为“结论→视觉证明→使用场景”，不得虚构新卖点凑数。`;
}

function countValue(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

// ============ Vue App ============
const app = createApp({
  setup() {

    // -------- 全局状态 --------
    const isProcessing = ref(false);
    const workflowRunning = ref(false);
    const userInput = ref('');
    const activeArtifact = ref(null);  // { title, content, editable, stepIndex, msgIndex }
    const chatMessages = ref(null);
    const chatInput = ref(null);
    const fileInput = ref(null);
    const viewMode = ref('workflow');
    const competitorUrl = ref('');
    const competitorImages = ref([]);
    const localReplacementImages = ref([]);
    const replacementOutputDirectory = ref('');
    const ownProductImage = ref(null);
    const ownProductFileInput = ref(null);
    const brandHandlingMode = ref('smart');
    const ownBrandName = ref('');
    const competitorBrandAliases = ref('');
    const brandDesignNotes = ref('适配图片风格');
    const ownBrandLogo = ref(null);
    const ownBrandLogoFileInput = ref(null);
    const replacementFilter = ref('all');
    const browserBusy = ref(false);
    const collecting = ref(false);
    const replacementRunning = ref(false);
    const replacementProgress = ref('');
    const replacementMessage = ref('');
    const replacementError = ref(false);
    let replacementController = null;
    const hostRequests = new Map();
    const replacementSaveRequests = new Map();
    let workflowController = null;
    let workflowSessionId = null;
    let workflowRunId = null;

    // -------- 会话管理 --------
    const sessions = ref([]);
    const activeSessionId = ref(null);

    const activeSession = computed(() =>
      sessions.value.find(s => s.id === activeSessionId.value) || null
    );

    const sessionsSorted = computed(() =>
      [...sessions.value].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
    );

    const stepProgressPercent = computed(() => {
      if (!activeSession.value) return 0;
      const done = (activeSession.value.completedSteps || []).length;
      return (done / 6) * 100;
    });

    // -------- 设置 --------
    const settings = reactive({
      llm: { configured: false, apiMode: '', model: '' },
      image: { configured: false, apiMode: '', model: '' },
      search: { enabled: false, configured: false }
    });

    const searchConfigured = computed(() => settings.search.enabled && settings.search.configured);
    const automationReady = computed(() =>
      settings.llm.configured && settings.image.configured && searchConfigured.value
    );

    const activeReplacementImages = computed(() => viewMode.value === 'local-replacement'
      ? localReplacementImages.value
      : competitorImages.value);
    const replacementFilters = computed(() => viewMode.value === 'local-replacement'
      ? [{ value: 'all', label: '全部', count: localReplacementImages.value.length }]
      : [
          { value: 'all', label: '全部', count: competitorImages.value.length },
          { value: 'main', label: '主图', count: competitorImages.value.filter(image => image.kind === 'main').length },
          { value: 'sku', label: 'SKU 图', count: competitorImages.value.filter(image => image.kind === 'sku').length },
          { value: 'detail', label: '详情页图', count: competitorImages.value.filter(image => image.kind === 'detail').length }
        ]);
    const filteredCompetitorImages = computed(() => replacementFilter.value === 'all'
      ? activeReplacementImages.value
      : activeReplacementImages.value.filter(image => image.kind === replacementFilter.value));
    const selectedReplacementCount = computed(() => activeReplacementImages.value.filter(image => image.selected).length);
    const allVisibleSelected = computed(() => Boolean(filteredCompetitorImages.value.length) &&
      filteredCompetitorImages.value.every(image => image.selected));
    const canGenerateReplacement = computed(() =>
      settings.image.configured && Boolean(ownProductImage.value) && selectedReplacementCount.value > 0 && !replacementRunning.value &&
      (brandHandlingMode.value === 'off' || Boolean(ownBrandLogo.value || ownBrandName.value.trim()))
    );

    watch(viewMode, mode => {
      if (mode === 'replacement' || mode === 'local-replacement') replacementFilter.value = 'all';
    });

    const isFormValid = computed(() => {
      if (!activeSession.value) return false;
      const fd = activeSession.value.stepData.formData;
      const cc = activeSession.value.stepData.creativeConfirmation || {};
      const mainCount = countValue(cc.mainImageCount, 0);
      const detailCount = countValue(cc.detailScreenCount, 0);
      return fd.platform && fd.imageType && fd.productName && fd.category &&
             fd.specs && fd.sellingPoints && fd.targetAudience && fd.usageScene && fd.priceRange &&
             activeSession.value.stepData.productImage &&
             Boolean(cc.pageStrategy) &&
             mainCount >= 1 && mainCount <= 20 && detailCount >= 1 && detailCount <= 30;
    });

    const searchKeywords = computed(() => {
      if (!activeSession.value) return '';
      const fd = activeSession.value.stepData.formData;
      const parts = [];
      if (fd.platform) parts.push(PLATFORM_NAMES[fd.platform] || fd.platform);
      if (fd.category) parts.push(fd.category);
      if (fd.productName) parts.push(fd.productName);
      return parts.join(' ') || '';
    });

    // ============ 会话 CRUD ============

    function persistSessions() {
      // Base64 生成图通常很大；写入 localStorage 会在批量生成数张后触发配额错误，
      // 因此只持久化流程和提示词。当前页面内仍完整保留生成图，供预览和下载。
      const lightweight = sessions.value.map(session => ({
        ...session,
        messages: session.messages.map(message => ({
          ...message,
          imageUrl: typeof message.imageUrl === 'string' && message.imageUrl.startsWith('data:')
            ? null
            : message.imageUrl,
          generating: false
        }))
      }));
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(lightweight));
      } catch (error) {
        console.warn('会话数据超过浏览器存储容量，本次仅保留当前页面状态：', error);
      }
    }

    function loadSessions() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          sessions.value = JSON.parse(raw);
          let recoveredInterruptedTask = false;
          sessions.value.forEach(session => {
            const confirmation = session.stepData?.creativeConfirmation || {};
            session.stepData.creativeConfirmation = {
              pageStrategy: confirmation.pageStrategy || 'minimal-selling-points',
              style: confirmation.style || '',
              mainImageCount: Number.parseInt(confirmation.mainImageCount, 10) || 5,
              detailScreenCount: Number.parseInt(confirmation.detailScreenCount, 10) || 10,
              contentRhythm: confirmation.contentRhythm || ''
            };
            const loadingCount = session.messages.filter(message => message.type === 'loading').length;
            if (loadingCount) {
              session.messages = session.messages.filter(message => message.type !== 'loading');
              session.messages.push({
                role: 'assistant',
                type: 'text',
                content: '⏹ 上次自动任务因页面或服务重启而中断，残留的等待状态已清理。可以重新提交完整流程。',
                timestamp: Date.now()
              });
              recoveredInterruptedTask = true;
            }
            session.messages.forEach(message => {
              if (message.type !== 'text') return;
              const matched = RETRY_ERROR_PREFIXES.find(([prefix]) =>
                String(message.content || '').startsWith(prefix)
              );
              if (!matched) return;
              message.type = 'retry';
              message.retryStep = matched[1];
              if (!String(message.content).includes('只重试当前步骤')) {
                message.content += '\n\n可以只重试当前步骤；已经成功的前置步骤不会重复调用。';
              }
              recoveredInterruptedTask = true;
            });
          });
          if (recoveredInterruptedTask) persistSessions();
        }
        if (!localStorage.getItem(BUILTIN_EXAMPLE_VERSION_KEY)) {
          const example = window.OUTOCUT_BUILTIN_EXAMPLE_SESSION;
          const alreadyPresent = sessions.value.find(session =>
            session.id === example?.id ||
            (session.name === example?.name && session.createdAt === example?.createdAt)
          );
          if (example && !alreadyPresent) {
            sessions.value.push(JSON.parse(JSON.stringify(example)));
            persistSessions();
          } else if (alreadyPresent && !alreadyPresent.isBuiltInExample) {
            alreadyPresent.isBuiltInExample = true;
            persistSessions();
          }
          localStorage.setItem(BUILTIN_EXAMPLE_VERSION_KEY, 'seeded');
        }
      } catch { sessions.value = []; }
    }

    function createSession_() {
      if (workflowRunning.value) return;
      const s = createSession();
      sessions.value.push(s);
      activeSessionId.value = s.id;
      // 欢迎消息 + 表单
      addMsg(s, 'assistant', 'text', '你好！我是电商AI图片提示词生成助手 ✨\n请在下方填写产品信息，填写完成后点击提交，我将为你进行专业的电商视觉分析。');
      addMsg(s, 'assistant', 'form-input', '');
      persistSessions();
      scrollChat();
    }

    function selectSession(id) {
      activeSessionId.value = id;
      activeArtifact.value = null;
      nextTick(() => scrollChat());
    }

    function deleteSession_(id) {
      const idx = sessions.value.findIndex(s => s.id === id);
      if (idx === -1) return;
      if (workflowSessionId === id) stopAutomation();
      sessions.value.splice(idx, 1);
      if (activeSessionId.value === id) {
        activeSessionId.value = sessions.value.length ? sessions.value[0].id : null;
      }
      activeArtifact.value = null;
      persistSessions();
    }

    // ============ 消息工具 ============

    function addMsg(session, role, type, content, extra) {
      const message = { role, type, content, timestamp: Date.now(), ...extra };
      session.messages.push(message);
      persistSessions();
      nextTick(() => scrollChat());
      return message;
    }

    function removeLoadingMsg(session) {
      const idx = session.messages.findIndex(m => m.type === 'loading');
      if (idx !== -1) session.messages.splice(idx, 1);
    }

    function removeAllLoadingMessages(session) {
      session.messages = session.messages.filter(message => message.type !== 'loading');
    }

    function addStepRetryError(session, prefix, error, retryStep) {
      addMsg(
        session,
        'assistant',
        'retry',
        `❌ ${prefix}：${error?.message || String(error)}\n\n可以只重试当前步骤；已经成功的前置步骤不会重复调用。`,
        { retryStep }
      );
    }

    function workflowSignal(session) {
      return workflowSessionId === session?.id ? workflowController?.signal : undefined;
    }

    function workflowId(session) {
      return workflowSessionId === session?.id ? workflowRunId : undefined;
    }

    function isAbortError(error) {
      return Boolean(error && error.name === 'AbortError');
    }

    function isExplicitTransientProviderError(error) {
      const message = String(error?.message || '').toLowerCase();
      return message.includes('please try again') || message.includes('try again later');
    }

    function waitForRetry(delayMs, signal) {
      return new Promise((resolve, reject) => {
        if (signal?.aborted) {
          reject(new DOMException('Aborted', 'AbortError'));
          return;
        }
        const timer = setTimeout(resolve, delayMs);
        signal?.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(new DOMException('Aborted', 'AbortError'));
        }, { once: true });
      });
    }

    async function callTextWithOneTransientRetry(s, prompt, systemPrompt, options, label) {
      try {
        return await ApiClient.callTextLLM(prompt, systemPrompt, options);
      } catch (error) {
        if (!isExplicitTransientProviderError(error) || options?.signal?.aborted) throw error;
        addMsg(s, 'assistant', 'text', `⚠️ ${label}遇到供应商临时错误，3 秒后自动重试一次；已完成的前置步骤不会重复调用。`);
        await waitForRetry(3000, options?.signal);
        return ApiClient.callTextLLM(prompt, systemPrompt, options);
      }
    }

    function stopAutomation() {
      if (!workflowRunning.value) return;
      const session = sessions.value.find(item => item.id === workflowSessionId);
      const runId = workflowRunId;
      if (runId) void ApiClient.cancelWorkflow(runId).catch(error => {
        console.warn('通知本地引擎中止请求失败：', error);
      });
      workflowController?.abort();
      workflowController = null;
      workflowSessionId = null;
      workflowRunId = null;
      workflowRunning.value = false;
      isProcessing.value = false;
      if (session) {
        removeAllLoadingMessages(session);
        addMsg(
          session,
          'assistant',
          'text',
          '⏹ 已中止自动生成。后续步骤和图片不会继续调用；已经发送给供应商的当前请求可能仍会被供应商处理。'
        );
      }
    }

    async function retryFailedStep(step, messageIndex) {
      const s = activeSession.value;
      if (!s || workflowRunning.value || isProcessing.value) return;
      const runners = {
        research: session => startAutoResearch(session, { reuseSearch: true }),
        variables: autoAdvanceStep3,
        strategy: autoAdvanceStep4,
        prompts: autoAdvanceStep6
      };
      const runner = runners[step];
      if (!runner) return;

      if (s.messages[messageIndex]?.type === 'retry') s.messages.splice(messageIndex, 1);
      const controller = new AbortController();
      workflowController = controller;
      workflowSessionId = s.id;
      workflowRunId = `workflow_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      workflowRunning.value = true;
      persistSessions();
      try {
        await runner(s);
      } finally {
        if (workflowController === controller) {
          workflowController = null;
          workflowSessionId = null;
          workflowRunId = null;
          workflowRunning.value = false;
        }
      }
    }

    function scrollChat() {
      const el = chatMessages.value;
      if (el) el.scrollTop = el.scrollHeight;
    }

    // ============ 步骤访问 ============

    function canAccessStep(index) {
      if (!activeSession.value) return false;
      if (index === 0) return true;
      return (activeSession.value.completedSteps || []).includes(index - 1);
    }

    function goToStep(index) {
      // 纯展示，不跳转步骤
    }

    function markStepDone(session, stepIndex) {
      if (!session.completedSteps.includes(stepIndex)) {
        session.completedSteps.push(stepIndex);
      }
      session.currentStep = stepIndex + 1;
      persistSessions();
    }

    // ============ Step 1: 提交产品信息 ============

    async function submitStep1() {
      const s = activeSession.value;
      if (!s || !isFormValid.value || !automationReady.value || isProcessing.value) return;

      const fd = s.stepData.formData;
      addMsg(s, 'user', 'user', `已提交产品信息：${fd.productName}（${PLATFORM_NAMES[fd.platform] || fd.platform}）`);

      // 生成输入确认卡
      const imageTypeNames = { main: '主图', detail: '详情页', both: '主图 + 详情页' };
      const summary = buildInputSummary(
        fd,
        s.stepData.productImage,
        imageTypeNames,
        s.stepData.creativeConfirmation
      );

      addMsg(s, 'assistant', 'text', '📋 产品信息已收到！以下是输入确认：');

      // 添加卡片
      addMsg(s, 'assistant', 'card', summary, {
        title: '📋 产品信息汇总',
        stepIndex: 0,
        cardType: 'input-summary'
      });

      markStepDone(s, 0);

      // 用户只提交一次，后续步骤全部自动执行。
      addMsg(s, 'assistant', 'text', '已进入自动生成流程：竞品研究 → 变量推荐 → 差异化策略 → 平台尺寸 → 提示词 → 图片。');
      const controller = new AbortController();
      workflowController = controller;
      workflowSessionId = s.id;
      workflowRunId = `workflow_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      workflowRunning.value = true;
      try {
        await startAutoResearch(s);
      } finally {
        if (workflowController === controller) {
          workflowController = null;
          workflowSessionId = null;
          workflowRunId = null;
          workflowRunning.value = false;
        }
      }
    }

    function buildInputSummary(fd, image, imageTypeNames, confirmation) {
      return `【产品信息汇总】

一、主视觉输入
- 主输入图片：${image ? '已上传' : '未上传'}
- 产品版本：${fd.productVersion || '未填写'}
- 视觉备注：${fd.visualNotes || '无'}

二、平台与任务范围
- 目标平台：${PLATFORM_NAMES[fd.platform] || fd.platform}
- 图片类型：${imageTypeNames[fd.imageType] || fd.imageType}

三、产品基础信息
- 产品名称：${fd.productName}
- 产品类目：${fd.category}
- 核心参数：${fd.specs}

四、销售表达锚点
- 核心卖点：${fd.sellingPoints}
- 目标人群：${fd.targetAudience}
- 使用场景：${fd.usageScene}
- 价格带：${fd.priceRange}

五、品牌与合规
- 品牌名：${fd.brandName || '无'}
- 品牌元素：${fd.brandElements || '无'}
- 避忌内容：${fd.avoidContent || '无'}

六、创意变量
- 页面编排：${PAGE_STRATEGY_NAMES[confirmation.pageStrategy] || '极简卖点拆页'}
- 主图张数：${countValue(confirmation.mainImageCount, 5)}张
- 详情页屏数：${countValue(confirmation.detailScreenCount, 10)}屏
- 视觉风格：${confirmation.style || '由变量建议自动确定'}
- 内容节奏：${confirmation.contentRhythm || '由变量建议自动确定'}`;
    }

    // ============ Step 2: 竞品研究 ============

    async function startAutoResearch(s, options = {}) {
      if (!s || !searchConfigured.value) return;

      let competitorInfo = options.reuseSearch ? String(s.stepData.competitorSearchContext || '') : '';
      addMsg(
        s,
        'assistant',
        'loading',
        competitorInfo ? '正在复用已完成的竞品搜索结果...' : '正在搜索竞品信息...'
      );
      isProcessing.value = true;

      try {
        if (!competitorInfo) {
          const searchResult = await ApiClient.callTavilySearch(searchKeywords.value, {
            maxResults: 10,
            signal: workflowSignal(s),
            workflowId: workflowId(s)
          });
          competitorInfo = '以下是联网搜索到的竞品信息：\n\n';
          if (searchResult.results?.length) {
            searchResult.results.forEach((r, i) => {
              competitorInfo += `### 竞品${i + 1}: ${r.title}\n- 链接: ${r.url}\n- 摘要: ${r.content}\n\n`;
            });
          }
          if (searchResult.answer) competitorInfo += `### 搜索摘要\n${searchResult.answer}\n\n`;
          s.stepData.competitorSearchContext = competitorInfo;
          persistSessions();
        }

        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'loading', '正在使用AI分析竞品（最长等待5分钟，可点击顶部“中止生成”）...');

        const requestOptions = {
          maxTokens: 4096,
          timeoutSeconds: 300,
          signal: workflowSignal(s),
          workflowId: workflowId(s)
        };
        const result = await callTextWithOneTransientRetry(
          s,
          buildCompetitorPrompt(s, competitorInfo),
          STEP2_SYSTEM_PROMPT,
          requestOptions,
          '竞品分析'
        );
        s.stepData.competitorResearch = result;

        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'text', '🔍 竞品研究完成！');
        addMsg(s, 'assistant', 'card', result, { title: '🔍 竞品研究结构化摘要', stepIndex: 1, cardType: 'competitor-research' });

        markStepDone(s, 1);
        await autoAdvanceStep3(s);

      } catch (e) {
        removeLoadingMsg(s);
        if (!isAbortError(e)) addStepRetryError(s, '竞品研究失败', e, 'research');
      } finally {
        isProcessing.value = false;
        persistSessions();
      }
    }

    function buildCompetitorPrompt(s, competitorInfo) {
      const fd = s.stepData.formData;
      return `请基于以下产品信息和竞品资料，进行竞品分析：

## 产品信息
- 目标平台：${PLATFORM_NAMES[fd.platform] || fd.platform}
- 产品名称：${fd.productName}
- 产品类目：${fd.category}
- 核心参数：${fd.specs}
- 核心卖点：${fd.sellingPoints}
- 目标人群：${fd.targetAudience}
- 使用场景：${fd.usageScene}
- 价格带：${fd.priceRange}

## 竞品资料
${competitorInfo}

请按照系统提示词的格式要求，输出《竞品研究结构化摘要》。`;
    }

    // ============ Step 3: 变量推荐（自动） ============

    async function autoAdvanceStep3(s) {
      addMsg(s, 'assistant', 'text', '正在进入 **Step 3: 变量推荐**，AI正在分析最佳创意方向...');
      addMsg(s, 'assistant', 'loading', '正在生成变量推荐...');
      isProcessing.value = true;

      try {
        const fd = s.stepData.formData;
        const comp = s.stepData.competitorResearch || '';

        const imageTypeLabel = { main: '仅主图', detail: '仅详情页', both: '主图 + 详情页' }[fd.imageType] || fd.imageType;

        const userPrompt = `请基于以下产品信息和竞品研究结果，推荐最佳的创意变量：

## 产品信息
- 目标平台：${PLATFORM_NAMES[fd.platform] || fd.platform}
- 图片类型需求：${imageTypeLabel}
- 产品名称：${fd.productName}
- 产品类目：${fd.category}
- 核心参数：${fd.specs}
- 核心卖点：${fd.sellingPoints}
- 目标人群：${fd.targetAudience}
- 使用场景：${fd.usageScene}
- 价格带：${fd.priceRange}

## 竞品研究结果
${comp || '暂无'}

## 用户在第一步设置的变量
- 页面编排：${PAGE_STRATEGY_NAMES[s.stepData.creativeConfirmation.pageStrategy] || '极简卖点拆页'}
- 主图张数：${countValue(s.stepData.creativeConfirmation.mainImageCount, 5)}张（必须采用，不要改写）
- 详情页屏数：${countValue(s.stepData.creativeConfirmation.detailScreenCount, 10)}屏（必须采用，不要改写）
- 视觉风格：${s.stepData.creativeConfirmation.style || '未指定，请推荐'}
- 内容节奏：${s.stepData.creativeConfirmation.contentRhythm || '未指定，请推荐'}
${pageStrategyRules(s.stepData.creativeConfirmation)}

## 重要约束
用户选择的图片类型为「${imageTypeLabel}」。${fd.imageType === 'main' ? '用户只需要主图，请只推荐主图相关的变量（主图张数等），不要推荐详情页相关内容。' : fd.imageType === 'detail' ? '用户只需要详情页，请只推荐详情页相关的变量（详情页屏数等），不要推荐主图相关内容。' : '用户需要主图和详情页，请分别推荐两者的变量。'}

请按照系统提示词的格式要求，输出《变量建议卡》。`;

        const requestOptions = {
          maxTokens: 3000,
          signal: workflowSignal(s),
          workflowId: workflowId(s)
        };
        const result = await callTextWithOneTransientRetry(
          s,
          userPrompt,
          STEP3_SYSTEM_PROMPT,
          requestOptions,
          '变量推荐'
        );
        s.stepData.variableSuggestion = result;

        // 尝试自动提取推荐值
        const styleMatch = result.match(/视觉风格[\s\S]{0,500}?推荐值[：:]\s*([^\n]+)/);
        const mainMatch = result.match(/主图张数[\s\S]{0,500}?推荐值[：:]\s*(\d+)/);
        const detailMatch = result.match(/详情页屏数[\s\S]{0,500}?推荐值[：:]\s*(\d+)/);
        const rhythmMatch = result.match(/内容节奏[\s\S]{0,500}?推荐值[：:]\s*([^\n]+)/);

        const confirmation = s.stepData.creativeConfirmation;
        s.stepData.creativeConfirmation = {
          pageStrategy: confirmation.pageStrategy || 'minimal-selling-points',
          style: String(confirmation.style || '').trim() || (styleMatch ? styleMatch[1].trim() : ''),
          mainImageCount: countValue(confirmation.mainImageCount, mainMatch ? Number(mainMatch[1]) : 5),
          detailScreenCount: countValue(confirmation.detailScreenCount, detailMatch ? Number(detailMatch[1]) : 10),
          contentRhythm: String(confirmation.contentRhythm || '').trim() || (rhythmMatch ? rhythmMatch[1].trim() : '')
        };

        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'text', '🎯 变量推荐完成，已自动合并第一步设置并继续。');
        addMsg(s, 'assistant', 'card', result, { title: '🎯 变量建议卡', stepIndex: 2, cardType: 'variable-suggestion' });
        markStepDone(s, 2);
        await autoAdvanceStep4(s);

      } catch (e) {
        removeLoadingMsg(s);
        if (!isAbortError(e)) addStepRetryError(s, '变量推荐失败', e, 'variables');
      } finally {
        isProcessing.value = false;
        persistSessions();
      }
    }

    // ============ Step 4: 差异化策略（自动） ============

    async function autoAdvanceStep4(s) {
      addMsg(s, 'assistant', 'text', '正在进入 **Step 4: 差异化策略**...');
      addMsg(s, 'assistant', 'loading', '正在生成差异化策略...');
      isProcessing.value = true;

      try {
        const fd = s.stepData.formData;
        const cc = s.stepData.creativeConfirmation;
        const comp = s.stepData.competitorResearch || '';
        const imageTypeLabel = { main: '仅主图', detail: '仅详情页', both: '主图 + 详情页' }[fd.imageType] || fd.imageType;

        const userPrompt = `请基于以下信息生成差异化策略：

## 产品信息
- 目标平台：${PLATFORM_NAMES[fd.platform] || fd.platform}
- 图片类型需求：${imageTypeLabel}
- 产品名称：${fd.productName}
- 产品类目：${fd.category}
- 核心参数：${fd.specs}
- 核心卖点：${fd.sellingPoints}
- 目标人群：${fd.targetAudience}
- 使用场景：${fd.usageScene}
- 价格带：${fd.priceRange}

## 竞品研究结果
${comp || '暂无'}

## 已确认的变量
- 页面编排：${PAGE_STRATEGY_NAMES[cc.pageStrategy] || '极简卖点拆页'}
- 视觉风格：${cc.style || '结合产品和竞品研究自动确定'}
- 主图张数：${countValue(cc.mainImageCount, 5)}张
- 详情页屏数：${countValue(cc.detailScreenCount, 10)}屏
- 内容节奏：${cc.contentRhythm || '结合产品和竞品研究自动确定'}
${pageStrategyRules(cc)}

## 重要约束
用户选择的图片类型为「${imageTypeLabel}」。${fd.imageType === 'main' ? '用户只需要主图，请只输出主图相关的差异化策略，不要输出详情页相关内容。' : fd.imageType === 'detail' ? '用户只需要详情页，请只输出详情页相关的差异化策略，不要输出主图相关内容。' : '用户需要主图和详情页，请分别输出两者的差异化策略。'}

请按照系统提示词的格式要求，输出完整的《差异化策略》文档。`;

        const requestOptions = {
          maxTokens: 4096,
          signal: workflowSignal(s),
          workflowId: workflowId(s)
        };
        const result = await callTextWithOneTransientRetry(
          s,
          userPrompt,
          STEP4_SYSTEM_PROMPT,
          requestOptions,
          '差异化策略生成'
        );
        s.stepData.differentiationStrategy = result;

        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'text', '💡 差异化策略生成完成！');
        addMsg(s, 'assistant', 'card', result, { title: '💡 差异化策略', stepIndex: 3, cardType: 'strategy' });

        markStepDone(s, 3);
        await autoAdvanceStep5(s);

      } catch (e) {
        removeLoadingMsg(s);
        if (!isAbortError(e)) addStepRetryError(s, '差异化策略生成失败', e, 'strategy');
      } finally {
        isProcessing.value = false;
        persistSessions();
      }
    }

    // ============ Step 5: 平台尺寸（纯查表） ============

    async function autoAdvanceStep5(s) {
      const fd = s.stepData.formData;
      let sizeAdvice = null;
      if (fd.platform && window.PLATFORM_SIZE_TABLE) {
        sizeAdvice = window.PLATFORM_SIZE_TABLE[fd.platform] || null;
      }
      s.stepData.platformSizeAdvice = sizeAdvice;

      if (sizeAdvice) {
        const sizeText = `【平台尺寸建议】

- 平台：${PLATFORM_NAMES[fd.platform]}
- 主图尺寸：${sizeAdvice.mainImage.width}×${sizeAdvice.mainImage.height}px
- 主图比例：${sizeAdvice.mainImage.ratio}
- 主图格式：${sizeAdvice.mainImage.format}
- 详情页宽度：${sizeAdvice.detailPage.width}px
- 详情页每屏高度：${sizeAdvice.detailPage.heightRange}px
- 详情页格式：${sizeAdvice.detailPage.format}
${sizeAdvice.notes ? '- 备注：' + sizeAdvice.notes : ''}`;

        addMsg(s, 'assistant', 'text', '📐 平台尺寸适配完成（自动查表）：');
        addMsg(s, 'assistant', 'card', sizeText, { title: '📐 平台尺寸建议', stepIndex: 4, cardType: 'platform-size' });
      } else {
        addMsg(s, 'assistant', 'text', '📐 Step 5: 该平台暂无专用尺寸数据，将使用通用尺寸。');
      }

      markStepDone(s, 4);
      await autoAdvanceStep6(s);
    }

    // ============ Step 6: 提示词生成（自动） ============

    async function autoAdvanceStep6(s) {
      addMsg(s, 'assistant', 'text', '正在进入 **Step 6: 提示词生成**，这是最后一步！');
      addMsg(s, 'assistant', 'loading', '正在生成可复制提示词...');
      isProcessing.value = true;

      try {
        const fd = s.stepData.formData;
        const cc = s.stepData.creativeConfirmation;
        const comp = s.stepData.competitorResearch || '';
        const strat = s.stepData.differentiationStrategy || '';
        const ps = s.stepData.platformSizeAdvice;
        const imageTypeLabel = { main: '仅主图', detail: '仅详情页', both: '主图 + 详情页' }[fd.imageType] || fd.imageType;

        // 根据 imageType 构建约束指令
        let imageTypeConstraint = '';
        if (fd.imageType === 'main') {
          imageTypeConstraint = `
## ⚠️ 图片类型约束（必须严格遵守）
用户选择的图片类型为「仅主图」。你必须：
1. 只生成主图提示词（===主图X===），不要生成任何详情页提示词（===第X屏===）
2. 变量推荐中只保留主图张数，忽略详情页屏数
3. 差异化策略中只保留主图策略，忽略详情页屏结构
4. 如果系统提示词要求同时输出详情页，请忽略该要求，只输出主图部分`;
        } else if (fd.imageType === 'detail') {
          imageTypeConstraint = `
## ⚠️ 图片类型约束（必须严格遵守）
用户选择的图片类型为「仅详情页」。你必须：
1. 只生成详情页提示词（===第X屏===），不要生成任何主图提示词（===主图X===）
2. 变量推荐中只保留详情页屏数，忽略主图张数
3. 差异化策略中只保留详情页屏结构，忽略主图策略
4. 如果系统提示词要求同时输出主图，请忽略该要求，只输出详情页部分`;
        } else {
          imageTypeConstraint = `
## 图片类型
用户需要「主图 + 详情页」，请同时输出主图和详情页的完整提示词。`;
        }

        const userPrompt = `请基于以下信息生成可复制提示词：

## 产品信息
- 目标平台：${PLATFORM_NAMES[fd.platform] || fd.platform}
- 图片类型需求：${imageTypeLabel}
- 产品名称：${fd.productName}
- 产品类目：${fd.category}
- 核心参数：${fd.specs}
- 核心卖点：${fd.sellingPoints}
- 目标人群：${fd.targetAudience}
- 使用场景：${fd.usageScene}
- 价格带：${fd.priceRange}
- 品牌/店铺：${fd.brandName || '无品牌强调'}
- 必须保留元素：${fd.brandElements || '无特殊要求'}
- 必须避开内容：${fd.avoidContent || '无限制'}
- 产品版本：${fd.productVersion || '未指定'}
- 视觉注意事项：${fd.visualNotes || '无'}

## 竞品研究结果
${comp || '暂无'}

## 平台尺寸建议
- 主图尺寸：${ps ? ps.mainImage.width + '×' + ps.mainImage.height + 'px' : '800×800px'}
- 主图比例：${ps ? ps.mainImage.ratio : '1:1'}
- 详情页宽度：${ps ? ps.detailPage.width + 'px' : '750px'}
- 详情页每屏高度：${ps ? ps.detailPage.heightRange + 'px' : '1000-1500px'}

## 已确认的变量
- 页面编排：${PAGE_STRATEGY_NAMES[cc.pageStrategy] || '极简卖点拆页'}
- 视觉风格：${cc.style || '结合产品和竞品研究自动确定'}
- 主图张数：${countValue(cc.mainImageCount, 5)}张
- 详情页屏数：${countValue(cc.detailScreenCount, 10)}屏
- 内容节奏：${cc.contentRhythm || '结合产品和竞品研究自动确定'}
${pageStrategyRules(cc)}

## 差异化策略
${strat || '暂无'}
${imageTypeConstraint}

## 数量与成品完整性硬性要求
- 主图必须严格输出 ${countValue(cc.mainImageCount, 5)} 条；详情页必须严格输出 ${countValue(cc.detailScreenCount, 10)} 屏（仍需服从上面的图片类型约束）。
- 每条可复制提示词都必须要求直接填入与本图任务匹配的简洁中文标题、卖点、参数或角标内容。
- 允许服务于视觉聚焦的纯色、渐变、水面、柔光等有意图负空间；不得出现文案占位、后期替换、可编辑区域、待填写项或方括号模板，画面必须是内容完整的成品。

请按照系统提示词的格式要求，使用 ===主图X=== 和 ===第X屏=== 分隔符，输出提示词。${fd.imageType === 'main' ? '注意：只输出主图提示词，不要输出详情页。' : fd.imageType === 'detail' ? '注意：只输出详情页提示词，不要输出主图。' : ''}`;

        const requestOptions = {
          maxTokens: 8192,
          signal: workflowSignal(s),
          workflowId: workflowId(s)
        };
        const result = await callTextWithOneTransientRetry(
          s,
          userPrompt,
          STEP6_SYSTEM_PROMPT,
          requestOptions,
          '提示词生成'
        );
        s.stepData.finalPrompts = result;
        parsePrompts(s, result);

        removeLoadingMsg(s);

        // 为每张提示词生成独立的 prompt-card 消息
        const mainPrompts = s.stepData.mainImagePrompts || [];
        const detailPromptsList = s.stepData.detailPrompts || [];
        const promptBatchId = `prompt_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

        if (mainPrompts.length === 0 && detailPromptsList.length === 0) {
          // 解析失败，降级为单个大卡片
          addMsg(s, 'assistant', 'text', '✨ 提示词已生成（未能拆分为独立卡片，以整体展示）：');
          addMsg(s, 'assistant', 'card', result, { title: '✨ 完整提示词', stepIndex: 5, cardType: 'final-prompts' });
        } else {
          if (mainPrompts.length > 0) {
            addMsg(s, 'assistant', 'text', `✨ 主图提示词已拆分为 ${mainPrompts.length} 张卡片，即将自动生成图片：`);
            mainPrompts.forEach((p, i) => {
              const meta = [p.goal, p.size, p.ratio].filter(Boolean).join(' · ');
              addMsg(s, 'assistant', 'prompt-card', p.text, {
                cardLabel: `主图 ${i + 1}`,
                cardMeta: meta,
                generating: false,
                imageUrl: null,
                genError: null,
                promptBatchId,
                promptKind: 'main'
              });
            });
          }
          if (detailPromptsList.length > 0) {
            addMsg(s, 'assistant', 'text', `✨ 详情页提示词已拆分为 ${detailPromptsList.length} 张卡片，即将自动生成图片：`);
            detailPromptsList.forEach((p, i) => {
              const meta = [p.goal, p.size, p.layout].filter(Boolean).join(' · ');
              addMsg(s, 'assistant', 'prompt-card', p.text, {
                cardLabel: `第 ${i + 1} 屏`,
                cardMeta: meta,
                generating: false,
                imageUrl: null,
                genError: null,
                promptBatchId,
                promptKind: 'detail'
              });
            });
          }
        }

        markStepDone(s, 5);
        await autoGenerateAllImages(s, promptBatchId);

      } catch (e) {
        removeLoadingMsg(s);
        if (!isAbortError(e)) addStepRetryError(s, '提示词生成失败', e, 'prompts');
      } finally {
        isProcessing.value = false;
        persistSessions();
      }
    }

    // 解析提示词到 session.stepData
    function parsePrompts(s, text) {
      const mainPrompts = [];
      const mainRegex = /===\s*主图\s*(\d+)\s*===[\s\S]*?(?====\s*主图\s*\d+\s*===|===\s*第\s*\d+\s*屏\s*===|$)/g;
      let m;
      while ((m = mainRegex.exec(text)) !== null) {
        const block = m[0];
        const g = block.match(/- 图片目标[：:]\s*(.*)/);
        const sz = block.match(/- 适用尺寸建议[：:]\s*(.*)/);
        const r = block.match(/- 推荐比例[：:]\s*(.*)/);
        const t = block.match(/- 可复制提示词[：:]\s*([\s\S]*?)(?=- (?:画面文案内容|建议文案占位)|- 执行提醒|$)/);
        mainPrompts.push({
          goal: g?.[1] || '', size: sz?.[1] || '', ratio: r?.[1] || '',
          text: t?.[1]?.trim() || block
        });
      }
      s.stepData.mainImagePrompts = mainPrompts;

      const detailPromptsList = [];
      const detailRegex = /===\s*第\s*(\d+)\s*屏\s*===[\s\S]*?(?====\s*第\s*\d+\s*屏\s*===|$)/g;
      while ((m = detailRegex.exec(text)) !== null) {
        const block = m[0];
        const g = block.match(/- 本屏目标[：:]\s*(.*)/);
        const sz = block.match(/- 适用尺寸建议[：:]\s*(.*)/);
        const l = block.match(/- 推荐比例\/版式[：:]\s*(.*)/);
        const t = block.match(/- 可复制提示词[：:]\s*([\s\S]*?)(?=- (?:画面文案内容|建议文案占位)|- 执行提醒|$)/);
        detailPromptsList.push({
          goal: g?.[1] || '', size: sz?.[1] || '', layout: l?.[1] || '',
          text: t?.[1]?.trim() || block
        });
      }
      s.stepData.detailPrompts = detailPromptsList;
    }

    // ============ 提示词卡片操作 ============

    function updatePromptCardText(msgIndex, newText) {
      const s = activeSession.value;
      if (!s || msgIndex < 0 || msgIndex >= s.messages.length) return;
      s.messages[msgIndex].content = newText;
      persistSessions();
    }

    function copyPromptCard(msgIndex) {
      const s = activeSession.value;
      if (!s || msgIndex < 0 || msgIndex >= s.messages.length) return;
      const text = s.messages[msgIndex].content;
      navigator.clipboard?.writeText(text).then(() => {
        alert('已复制到剪贴板！');
      }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        alert('已复制到剪贴板！');
      });
    }

    async function generateImageFromCard(msgIndex) {
      const s = activeSession.value;
      if (!s || msgIndex < 0 || msgIndex >= s.messages.length) return;
      const msg = s.messages[msgIndex];
      await generateImageForMessage(s, msg);
    }

    function unfinishedPromptReason(text) {
      if (/\[[^\]\n]{1,100}\]/.test(text)) return '仍包含方括号模板内容';
      const unfinishedLine = String(text).split(/\r?\n/).find(line => {
        if (/不得|不要|避免|不留|无需/.test(line)) return false;
        return /预留.{0,24}(?:区|空间)|文案占位|后期替换|待填写|可编辑区域/.test(line);
      });
      return unfinishedLine ? '仍包含留白、占位或后期编辑要求' : '';
    }

    async function generateImageForMessage(s, msg, signal = workflowSignal(s)) {
      const promptText = msg.content;
      const imageBase64 = s.stepData.productImage;

      if (!imageBase64) {
        msg.genError = '请先在 Step 1 上传产品图片';
        persistSessions();
        return false;
      }

      const unfinished = unfinishedPromptReason(promptText);
      if (unfinished) {
        msg.genError = `未自动生成：${unfinished}`;
        persistSessions();
        return false;
      }

      msg.generating = true;
      msg.genError = null;
      msg.imageUrl = null;
      persistSessions();

      try {
        const imageUrl = await ApiClient.callImageGeneration(promptText, imageBase64, {
          signal,
          workflowId: workflowId(s)
        });
        msg.imageUrl = imageUrl;
        return true;
      } catch (e) {
        if (!isAbortError(e)) msg.genError = e.message || '图片生成失败';
        return false;
      } finally {
        msg.generating = false;
        persistSessions();
      }
    }

    async function autoGenerateAllImages(s, promptBatchId) {
      const signal = workflowSignal(s);
      const batchCards = s.messages.filter(
        message => message.type === 'prompt-card' && message.promptBatchId === promptBatchId
      );
      if (!batchCards.length) {
        addMsg(s, 'assistant', 'text', '❌ 未解析出独立提示词，已停止自动生图，请检查完整提示词卡。');
        return;
      }

      const imageType = s.stepData.formData.imageType;
      const confirmation = s.stepData.creativeConfirmation;
      const expectedMain = imageType === 'detail' ? 0 : countValue(confirmation.mainImageCount, 5);
      const expectedDetail = imageType === 'main' ? 0 : countValue(confirmation.detailScreenCount, 10);
      const mainCards = batchCards.filter(card => card.promptKind === 'main');
      const detailCards = batchCards.filter(card => card.promptKind === 'detail');
      const cards = [
        ...mainCards.slice(0, expectedMain),
        ...detailCards.slice(0, expectedDetail)
      ];
      const missing = Math.max(0, expectedMain - mainCards.length) +
        Math.max(0, expectedDetail - detailCards.length);
      const extra = Math.max(0, mainCards.length - expectedMain) +
        Math.max(0, detailCards.length - expectedDetail);
      if (missing || extra) {
        addMsg(
          s,
          'assistant',
          'text',
          `⚠️ 提示词数量与设置不一致：应有主图 ${expectedMain}、详情页 ${expectedDetail}；` +
          `实际解析出主图 ${mainCards.length}、详情页 ${detailCards.length}。` +
          `${missing ? `缺少 ${missing} 张将记为失败；` : ''}${extra ? `多出的 ${extra} 张不会自动生成。` : ''}`
        );
      }

      const expectedTotal = expectedMain + expectedDetail;
      const progress = addMsg(s, 'assistant', 'text', `🎨 开始自动生成图片，计划 ${expectedTotal} 张；将按顺序生成以减少接口限流。`);
      let succeeded = 0;
      let failed = missing;
      for (let index = 0; index < cards.length; index += 1) {
        if (signal?.aborted) {
          progress.content = `⏹ 自动生图已中止：停止于第 ${index + 1}/${cards.length} 张。`;
          persistSessions();
          return;
        }
        progress.content = `🎨 正在自动生成第 ${index + 1}/${cards.length} 张：${cards[index].cardLabel}`;
        persistSessions();
        const generated = await generateImageForMessage(s, cards[index], signal);
        if (signal?.aborted) {
          progress.content = `⏹ 自动生图已中止：已成功生成 ${succeeded} 张。`;
          persistSessions();
          return;
        }
        if (generated) succeeded += 1;
        else failed += 1;
      }
      progress.content = failed
        ? `⚠️ 自动生图结束：成功 ${succeeded} 张，失败、缺少或提示词未通过完整性检查 ${failed} 张。失败项可修正后单独重试。`
        : `✅ 自动生图完成：${succeeded}/${expectedTotal} 张全部成功。`;
      persistSessions();
    }

    // ============ 用户自由聊天 ============

    async function sendUserMessage() {
      const s = activeSession.value;
      const text = userInput.value.trim();
      if (!s || !text || isProcessing.value) return;

      userInput.value = '';
      addMsg(s, 'user', 'user', text);

      // 构建上下文
      const fd = s.stepData.formData;
      const contextLines = [];
      if (fd.productName) contextLines.push(`产品：${fd.productName}，平台：${PLATFORM_NAMES[fd.platform] || fd.platform}`);
      if (s.stepData.competitorResearch) contextLines.push('已完成竞品研究');
      if (s.stepData.creativeConfirmation?.style) contextLines.push(`已确认变量：${s.stepData.creativeConfirmation.style}`);
      if (s.stepData.differentiationStrategy) contextLines.push('已完成差异化策略');
      if (s.stepData.finalPrompts) contextLines.push('已生成提示词');

      const systemContext = `你是一个专业的电商视觉策略AI助手。当前会话状态：${contextLines.join('；') || '刚开始'}。当前步骤：Step ${s.currentStep + 1}。请根据用户的问题给出专业、简洁的回答。如果用户要求修改之前的产出，请基于已有上下文进行调整。`;

      addMsg(s, 'assistant', 'loading', '思考中...');
      isProcessing.value = true;

      try {
        const result = await ApiClient.callTextLLM(text, systemContext, { maxTokens: 2048 });
        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'text', result);
      } catch (e) {
        removeLoadingMsg(s);
        addMsg(s, 'assistant', 'text', `❌ 回复失败：${e.message}`);
      } finally {
        isProcessing.value = false;
      }
    }

    // ============ 右侧产物面板 ============

    function openArtifact(msg) {
      activeArtifact.value = {
        title: msg.title || '产物',
        content: msg.content,
        editable: true,
        stepIndex: msg.stepIndex,
        msgIndex: activeSession.value?.messages.indexOf(msg)
      };
    }

    function closeArtifact() { activeArtifact.value = null; }

    function copyArtifactContent() {
      if (!activeArtifact.value) return;
      const text = activeArtifact.value.content;
      navigator.clipboard?.writeText(text).then(() => {
        alert('已复制到剪贴板！');
      }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        alert('已复制到剪贴板！');
      });
    }

    function saveArtifactEdit() {
      if (!activeArtifact.value || !activeSession.value) return;
      const s = activeSession.value;
      const mi = activeArtifact.value.msgIndex;
      if (mi >= 0 && mi < s.messages.length) {
        s.messages[mi].content = activeArtifact.value.content;
      }
      // 同步到 stepData
      const st = activeArtifact.value.stepIndex;
      if (st === 1) s.stepData.competitorResearch = activeArtifact.value.content;
      if (st === 5) s.stepData.finalPrompts = activeArtifact.value.content;
      persistSessions();
      alert('修改已保存！');
    }

    // ============ 图片上传 ============

    function triggerUpload(event) {
      const container = event.currentTarget;
      const input = container.querySelector('input[type="file"]');
      if (input) input.click();
    }

    function handleFileSelect(e) {
      const file = e.target.files?.[0];
      if (file) processImage(file);
    }

    function handleDrop(e) {
      const file = e.dataTransfer.files?.[0];
      if (file?.type.startsWith('image/')) processImage(file);
    }

    function processImage(file) {
      const s = activeSession.value;
      if (!s) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        s.stepData.productImage = e.target.result;
        persistSessions();
      };
      reader.readAsDataURL(file);
    }

    // ============ 竞品换品 ============

    function callHostCompetitor(action, payload) {
      if (window.parent === window) return Promise.reject(new Error('请在 OutoCut 桌面端中使用商品页采集'));
      const requestId = `competitor_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          hostRequests.delete(requestId);
          reject(new Error(action === 'collect' ? '图片拉取超时，请确认商品页已加载完成' : '浏览器响应超时'));
        }, action === 'collect' ? 180000 : 120000);
        hostRequests.set(requestId, { resolve, reject, timer });
        window.parent.postMessage({
          type: 'outocut-competitor-browser',
          requestId,
          action,
          ...(payload || {})
        }, '*');
      });
    }

    function setReplacementMessage(message, error = false) {
      replacementMessage.value = message;
      replacementError.value = error;
    }

    async function openCompetitorBrowser() {
      if (!competitorUrl.value || browserBusy.value) return;
      browserBusy.value = true;
      setReplacementMessage('正在打开浏览器，请在新窗口中完成登录并停留在商品详情页…');
      try {
        const status = await callHostCompetitor('open', { url: competitorUrl.value });
        if (status.pageUrl) competitorUrl.value = status.pageUrl;
        setReplacementMessage(`浏览器已打开${status.title ? `：${status.title}` : ''}。完成登录后返回此处拉取图片。`);
      } catch (error) {
        setReplacementMessage(error.message || String(error), true);
      } finally {
        browserBusy.value = false;
      }
    }

    async function collectCompetitorImages() {
      if (collecting.value) return;
      collecting.value = true;
      setReplacementMessage('正在滚动页面、识别并下载商品图片，请稍候…');
      try {
        const collection = await callHostCompetitor('collect');
        competitorUrl.value = collection.pageUrl || competitorUrl.value;
        competitorImages.value = (collection.images || []).map(image => ({
          ...image,
          sourceType: 'browser',
          selected: true,
          generating: false,
          resultUrl: null,
          error: '',
          downloadRequestId: null
        }));
        const counts = replacementFilters.value.slice(1).map(item => `${item.label} ${item.count}`).join('、');
        setReplacementMessage(`已从${collection.title ? `“${collection.title}”` : '当前页面'}拉取 ${competitorImages.value.length} 张图片：${counts}。自动分类可能受页面结构影响，请生成前检查。`);
      } catch (error) {
        setReplacementMessage(error.message || String(error), true);
      } finally {
        collecting.value = false;
      }
    }

    function isSupportedReplacementImage(file) {
      return Boolean(file) && (
        /^image\/(png|jpeg|webp)$/i.test(file.type || '') ||
        /\.(png|jpe?g|webp)$/i.test(file.name || '')
      );
    }

    function localImageSignature(file) {
      return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
    }

    function readFileAsDataUrl(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error(`${file?.name || '图片'} 读取失败`));
        reader.onload = event => resolve(event.target?.result);
        reader.readAsDataURL(file);
      });
    }

    function readLocalReplacementFile(file, kind, index) {
      return new Promise((resolve, reject) => {
        const previewUrl = URL.createObjectURL(file);
        const probe = new Image();
        probe.onerror = () => {
          URL.revokeObjectURL(previewUrl);
          reject(new Error(`${file.name} 不是有效图片`));
        };
        probe.onload = () => resolve({
            id: `local_${Date.now()}_${index}_${Math.random().toString(36).slice(2, 7)}`,
            dataUrl: null,
            previewUrl,
            file,
            alt: file.name,
            sourceName: file.name,
            sourcePath: file.webkitRelativePath || file.name,
            sourceType: 'local',
            fileSignature: localImageSignature(file),
            width: probe.naturalWidth,
            height: probe.naturalHeight,
            kind,
            selected: true,
            generating: false,
            resultUrl: null,
            error: '',
            downloadRequestId: null
          });
        probe.src = previewUrl;
      });
    }

    async function addLocalReplacementFiles(fileList) {
      if (replacementRunning.value) return;
      const files = Array.from(fileList || []);
      if (!files.length) return;

      const existing = new Set(localReplacementImages.value.map(image => image.fileSignature));
      const valid = [];
      let unsupported = 0;
      let oversized = 0;
      let duplicate = 0;
      files.forEach(file => {
        const signature = localImageSignature(file);
        if (!isSupportedReplacementImage(file)) unsupported += 1;
        else if (file.size > 20 * 1024 * 1024) oversized += 1;
        else if (existing.has(signature)) duplicate += 1;
        else {
          existing.add(signature);
          valid.push(file);
        }
      });

      const loaded = [];
      let failed = 0;
      for (let offset = 0; offset < valid.length; offset += 20) {
        const batch = valid.slice(offset, offset + 20);
        const results = await Promise.allSettled(batch.map((file, index) =>
          readLocalReplacementFile(
            file,
            'local',
            offset + index
          )));
        loaded.push(...results.filter(result => result.status === 'fulfilled').map(result => result.value));
        failed += results.filter(result => result.status === 'rejected').length;
      }
      localReplacementImages.value.push(...loaded);

      const skipped = [];
      if (unsupported) skipped.push(`${unsupported} 张格式不支持`);
     if (oversized) skipped.push(`${oversized} 张超过 20MB`);
     if (duplicate) skipped.push(`${duplicate} 张重复`);
     if (failed) skipped.push(`${failed} 张读取失败`);
     setReplacementMessage(
        `已上传 ${loaded.length} 张本地图片${skipped.length ? `；已跳过：${skipped.join('、')}` : ''}。`,
       !loaded.length && skipped.length > 0
     );
    }

   async function handleLocalReplacementSelect(event) {
      await addLocalReplacementFiles(event.target.files);
     event.target.value = '';
   }

   async function handleLocalReplacementFolderSelect(event) {
      await addLocalReplacementFiles(event.target.files);
     event.target.value = '';
    }

    function handleLocalReplacementDrop(event) {
      addLocalReplacementFiles(event.dataTransfer.files);
    }

    function removeLocalReplacementImage(image) {
      if (replacementRunning.value) return;
      if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
      localReplacementImages.value = localReplacementImages.value.filter(item => item.id !== image.id);
      setReplacementMessage('已从本地换品列表移除 1 张图片。');
    }

    function clearLocalReplacementImages() {
      if (replacementRunning.value) return;
      const count = localReplacementImages.value.length;
      if (!count || !window.confirm(`确定清空 ${count} 张本地参考图及其生成结果吗？`)) return;
      localReplacementImages.value.forEach(image => {
        if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
      });
      localReplacementImages.value = [];
      setReplacementMessage(`已清空 ${count} 张本地参考图。`);
    }

    async function selectReplacementOutputDirectory() {
      if (window.parent === window) {
        setReplacementMessage('请在 OutoCut 桌面端中设置自动下载路径。', true);
        return;
      }
      const requestId = `replacement_directory_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      try {
        const result = await new Promise((resolve, reject) => {
          const timer = window.setTimeout(() => {
            hostRequests.delete(requestId);
            reject(new Error('目录选择响应超时'));
          }, 120000);
          hostRequests.set(requestId, { resolve, reject, timer });
          window.parent.postMessage({ type: 'outocut-select-replacement-output-directory', requestId }, '*');
        });
        if (!result?.path) return;
        replacementOutputDirectory.value = result.path;
        setReplacementMessage(`生成成功后将自动保存到：${result.path}`);
      } catch (error) {
        setReplacementMessage(error.message || String(error), true);
      }
    }

    function validateOwnProductFile(file) {
      if (!file || !/^image\/(png|jpeg|webp)$/i.test(file.type)) throw new Error('请选择 PNG、JPG 或 WebP 产品图片');
      if (file.size > 20 * 1024 * 1024) throw new Error('产品图片不能超过 20MB');
      return file;
    }

    function loadOwnProductFile(file) {
      try {
        validateOwnProductFile(file);
      } catch (error) {
        setReplacementMessage(error.message || String(error), true);
        return;
      }
      const reader = new FileReader();
      reader.onload = event => {
        ownProductImage.value = event.target.result;
        setReplacementMessage('产品图已就绪。请选择需要替换的竞品图片。');
      };
      reader.onerror = () => setReplacementMessage('读取产品图片失败', true);
      reader.readAsDataURL(file);
    }

    function handleOwnProductSelect(event) {
      const file = event.target.files?.[0];
      if (file) loadOwnProductFile(file);
      event.target.value = '';
    }

    function handleOwnProductDrop(event) {
      const file = event.dataTransfer.files?.[0];
      if (file) loadOwnProductFile(file);
    }

    function loadOwnBrandLogo(file) {
      if (!file || !/^image\/(png|jpeg|webp)$/i.test(file.type)) {
        setReplacementMessage('品牌 Logo 请选择 PNG、JPG 或 WebP 图片', true);
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        setReplacementMessage('品牌 Logo 不能超过 10MB', true);
        return;
      }
      const reader = new FileReader();
      reader.onload = event => {
        ownBrandLogo.value = event.target.result;
        setReplacementMessage('品牌 Logo 已就绪。AI 将把它作为第三张参考图，自动识别并重新设计竞品品牌位置。');
      };
      reader.onerror = () => setReplacementMessage('读取品牌 Logo 失败', true);
      reader.readAsDataURL(file);
    }

    function handleOwnBrandLogoSelect(event) {
      const file = event.target.files?.[0];
      if (file) loadOwnBrandLogo(file);
      event.target.value = '';
    }

    function toggleVisibleSelection() {
      const next = !allVisibleSelected.value;
      filteredCompetitorImages.value.forEach(image => { image.selected = next; });
    }

    function kindLabel(kind) {
      return { main: '主图', sku: 'SKU 图', detail: '详情页图' }[kind] || '图片';
    }

    function replacementPrompt(image) {
      const prompt = [
        'Image 1 is the target e-commerce design/template. Image 2 is our replacement product identity reference.',
        'Replace only the product object shown in Image 1 with the exact product from Image 2.',
        'Preserve every non-product element from Image 1 unchanged: canvas dimensions and aspect ratio, crop, camera angle, layout, background, lighting, shadows, reflections, props, people, typography, Chinese copy, labels, badges, icons, colors and decorations.',
        'Do not copy the background, text, props or composition from Image 2. Do not redesign, translate, rewrite, add or remove any non-product element.',
        'Preserve the replacement product identity faithfully, including silhouette, structure, material, color, logo and packaging details visible in Image 2. Do not invent variants.',
        'Place it at the original product position and match the original scale, perspective, occlusion, contact shadow and scene lighting.'
      ];
      if (brandHandlingMode.value !== 'off') {
        const brandName = ownBrandName.value.trim().replace(/[\r\n]+/g, ' ');
        const competitorAliases = competitorBrandAliases.value.trim().replace(/[\r\n]+/g, ' ');
        const designNotes = brandDesignNotes.value.trim().replace(/[\r\n]+/g, ' ');
        prompt.push(
          ownBrandLogo.value
            ? `Image 3 is our exact brand logo and visual identity reference. Use it faithfully; do not copy its background.${brandName ? ` Our brand name is exactly: "${brandName}".` : ''}`
            : `Our replacement brand name is exactly: "${brandName}". Spell it exactly and do not invent additional brand words.`,
          competitorAliases
            ? `Competitor brand identification hints supplied by the user: "${competitorAliases}". These may include Chinese names, English names, abbreviations or aliases. Treat them only as recognition clues; do not assume every image contains them.`
            : 'No competitor brand name was supplied. Identify competitor branding visually from logos, wordmarks, repeated packaging marks and brand-specific signatures.',
          'Visually inspect Image 1 for actual competitor logos, wordmarks and competitor brand names, including marks on packaging, background graphics, badges and decorative brand signatures.',
          'Preserve generic selling-point copy, specifications, product models, people, props, badges, certification marks and layout. Never treat a generic descriptive word as a brand merely because it resembles a supplied alias.'
        );
        if (brandHandlingMode.value === 'smart') {
          prompt.push(
            'Use conditional brand handling: only when competitor branding is visibly present, remove every confirmed competitor brand mark and redesign those exact brand placements using our replacement brand identity.',
            'If Image 1 contains no confirmed competitor branding, do not add a new logo, brand name, badge or extra text elsewhere in the scene. Branding already visible on the exact replacement product from Image 2 may remain naturally as part of that product.',
            'When a competitor mark is replaced, integrate our branding naturally with suitable scale, spacing, color, typography, curvature, perspective and lighting; it must look intentionally designed, not pasted on top.',
            'Do not leave any confirmed readable competitor brand name or logo in the finished image.'
          );
        } else {
          prompt.push(
            'Use forced brand placement: remove every confirmed competitor brand mark, then ensure our replacement brand has a clear but restrained, naturally designed presence even if Image 1 originally had no competitor branding.',
            'Prefer an existing brand position or a plausible product or packaging surface. Do not cover selling-point copy, people, key product details or certification marks, and do not scatter repeated logos.',
            'Integrate our branding with suitable scale, spacing, color, typography, curvature, perspective and lighting; it must look intentionally designed, not pasted on top.',
            'Do not leave any confirmed readable competitor brand name or logo in the finished image.'
          );
        }
        if (designNotes) prompt.push(`Brand redesign direction from the user: ${designNotes}`);
      }
      prompt.push(
        'Return one full-size finished opaque e-commerce image only. Do not return a cutout, transparent layer, mask or isolated object.',
        `The source image is classified as ${kindLabel(image.kind)}.`
      );
      return prompt.join('\n');
    }

    async function generateSelectedReplacements() {
      if (!canGenerateReplacement.value) return;
      const selected = activeReplacementImages.value.filter(image => image.selected);
      if (selected.length > 30 && !window.confirm(`本次将调用外部图片模型 ${selected.length} 次，可能耗时较长并产生相应费用。确定继续吗？`)) return;
      const shouldAutoSave = Boolean(replacementOutputDirectory.value) && selected.some(image => image.sourceType === 'local');
      replacementRunning.value = true;
      replacementController = new AbortController();
      let succeeded = 0;
      let failed = 0;
      let autoSaved = 0;
      let saveFailed = 0;
      setReplacementMessage(`即将调用当前外部图片模型，按顺序生成 ${selected.length} 张；每张只调用一次。`);
      for (let index = 0; index < selected.length; index += 1) {
        const image = selected[index];
        if (replacementController.signal.aborted) break;
        replacementProgress.value = `${index + 1}/${selected.length}`;
        image.generating = true;
        image.error = '';
        try {
          const templateImage = image.dataUrl || await readFileAsDataUrl(image.file);
          image.resultUrl = await ApiClient.callProductReplacement(templateImage, ownProductImage.value, {
            prompt: replacementPrompt(image),
            brandImageBase64: brandHandlingMode.value !== 'off' ? ownBrandLogo.value : null,
            signal: replacementController.signal,
            workflowId: `replacement_${Date.now()}_${index}`
          });
          succeeded += 1;
          if (image.sourceType === 'local' && replacementOutputDirectory.value) {
            try {
              const saveResult = await requestReplacementSave(image, true);
              if (saveResult.saved) autoSaved += 1;
            } catch (saveError) {
              image.error = `图片已生成，但自动保存失败：${saveError.message || String(saveError)}`;
              saveFailed += 1;
            }
          }
        } catch (error) {
          if (error.name === 'AbortError') break;
          image.error = error.message || '替换生成失败';
          failed += 1;
        } finally {
          image.generating = false;
        }
      }
      const stopped = replacementController.signal.aborted;
      replacementRunning.value = false;
      replacementController = null;
      replacementProgress.value = '';
      const saveSummary = shouldAutoSave
        ? `；自动保存 ${autoSaved} 张${saveFailed ? `，保存失败 ${saveFailed} 张` : ''}`
        : '';
      setReplacementMessage(stopped
        ? `已中止：成功 ${succeeded} 张，生成失败 ${failed} 张${saveSummary}。`
          : failed || saveFailed
          ? `处理结束：生成成功 ${succeeded} 张，生成失败 ${failed} 张${saveSummary}。问题项会保留原因。`
          : `替换完成：${succeeded} 张全部生成成功${saveSummary}。`, Boolean(failed || saveFailed));
    }

    function stopReplacement() {
      replacementController?.abort();
    }

    function replacementSuggestedName(image) {
      const sourceStem = String(image.sourceName || image.id || '图片').replace(/\.(?:png|jpe?g|webp)$/i, '');
      return `${image.sourceType === 'local' ? '本地换品' : '竞品换品'}-${sourceStem}.png`;
    }

    function requestReplacementSave(image, useAutomaticDirectory = false) {
      if (!image?.resultUrl || window.parent === window) return Promise.reject(new Error('当前环境不支持系统文件保存'));
      const requestId = `replacement_download_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      image.downloadRequestId = requestId;
      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          replacementSaveRequests.delete(requestId);
          image.downloadRequestId = null;
          reject(new Error('保存图片响应超时'));
        }, 180000);
        replacementSaveRequests.set(requestId, { resolve, reject, timer, image });
        window.parent.postMessage({
          type: 'outocut-save-generated-image',
          requestId,
         source: image.resultUrl,
         suggestedName: replacementSuggestedName(image),
         outputDirectory: useAutomaticDirectory ? replacementOutputDirectory.value : '',
          relativeDirectory: ''
        }, '*');
      });
    }

    async function downloadReplacement(image) {
      try {
        const result = await requestReplacementSave(
          image,
          image.sourceType === 'local' && Boolean(replacementOutputDirectory.value)
        );
        setReplacementMessage(result.saved ? `已保存到：${result.path}` : '已取消保存。');
      } catch (error) {
        setReplacementMessage(`保存失败：${error.message || String(error)}`, true);
      }
    }

    // ============ OutoCut 系统设置 ============

    async function loadSettings() {
      try {
        const current = await ApiClient.getConfiguration();
        Object.assign(settings.llm, current.llm || {});
        Object.assign(settings.image, current.image || {});
        Object.assign(settings.search, current.search || {});
      } catch (e) {
        console.error('读取 OutoCut 生成套图配置失败：', e);
      }
    }

    function openSystemSettings() {
      window.parent.postMessage({ type: 'outocut-open-settings' }, '*');
    }

    // ============ 工具函数 ============

    function getPreview(text) {
      if (!text) return '';
      return text.replace(/[\n\r]/g, ' ').substring(0, 120) + '...';
    }

    function formatMarkdown(text) {
      if (!text) return '';
      let html = text
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/`(.*?)`/g, '<code>$1</code>')
        .replace(/\n/g, '<br>');
      return html;
    }

    function autoResize(e) {
      const el = e.target;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 150) + 'px';
    }

    async function downloadGeneratedImage(messageIndex) {
      const message = activeSession.value?.messages?.[messageIndex];
      if (!message?.imageUrl || message.downloading) return;
      message.downloading = true;
      message.downloadMessage = '';
      message.downloadError = false;

      if (window.parent !== window) {
        const requestId = `download_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        message.downloadRequestId = requestId;
        window.parent.postMessage({
          type: 'outocut-save-generated-image',
          requestId,
          source: message.imageUrl,
          suggestedName: `${message.cardLabel || '生成图片'}.png`
        }, '*');
        return;
      }

      try {
        const response = await fetch(message.imageUrl);
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = `${message.cardLabel || '生成图片'}.png`;
        anchor.click();
        URL.revokeObjectURL(objectUrl);
        message.downloadMessage = '图片已下载。';
      } catch (error) {
        message.downloadError = true;
        message.downloadMessage = `保存失败：${error.message || String(error)}`;
      } finally {
        message.downloading = false;
      }
    }

    function handleHostMessage(event) {
      if (event.data?.type === 'outocut-refresh-settings') {
        loadSettings();
        return;
      }
      if (event.data?.type === 'outocut-competitor-browser-result') {
        const pending = hostRequests.get(event.data.requestId);
        if (!pending) return;
        window.clearTimeout(pending.timer);
        hostRequests.delete(event.data.requestId);
        if (event.data.error) pending.reject(new Error(event.data.error));
        else pending.resolve(event.data.result || {});
        return;
      }
      if (event.data?.type === 'outocut-select-replacement-output-directory-result') {
        const pending = hostRequests.get(event.data.requestId);
        if (!pending) return;
        window.clearTimeout(pending.timer);
        hostRequests.delete(event.data.requestId);
        if (event.data.error) pending.reject(new Error(event.data.error));
        else pending.resolve({ path: event.data.path || '' });
        return;
      }
      if (event.data?.type !== 'outocut-save-generated-image-result') return;
      const pendingSave = replacementSaveRequests.get(event.data.requestId);
      if (pendingSave) {
        window.clearTimeout(pendingSave.timer);
        replacementSaveRequests.delete(event.data.requestId);
        pendingSave.image.downloadRequestId = null;
        if (event.data.error) pendingSave.reject(new Error(event.data.error));
        else pendingSave.resolve({ saved: Boolean(event.data.saved), path: event.data.path || '' });
        return;
      }
      const message = activeSession.value?.messages?.find(item => item.downloadRequestId === event.data.requestId);
      const replacement = [...competitorImages.value, ...localReplacementImages.value]
        .find(item => item.downloadRequestId === event.data.requestId);
      if (!message && !replacement) return;
      if (replacement) {
        replacement.downloadRequestId = null;
        setReplacementMessage(event.data.error
          ? `保存失败：${event.data.error}`
          : event.data.saved
            ? `已保存到：${event.data.path}`
            : '已取消保存。', Boolean(event.data.error));
        return;
      }
      message.downloading = false;
      message.downloadRequestId = null;
      message.downloadError = Boolean(event.data.error);
      message.downloadMessage = event.data.error
        ? `保存失败：${event.data.error}`
        : event.data.saved
          ? `已保存到：${event.data.path}`
          : '已取消保存。';
    }

    // ============ 生命周期 ============

    onMounted(() => {
      loadSettings();
      window.addEventListener('message', handleHostMessage);
      loadSessions();
      if (sessions.value.length && !activeSessionId.value) {
        activeSessionId.value = sessions.value[0].id;
      }
    });
    onBeforeUnmount(() => {
      window.removeEventListener('message', handleHostMessage);
      localReplacementImages.value.forEach(image => {
        if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
      });
    });

    // ============ 返回 ============

    return {
      // 全局
      isProcessing, workflowRunning, userInput, viewMode,
      activeArtifact, chatMessages, chatInput, fileInput,
      steps: STEPS,

      // 会话
      sessions, activeSessionId, activeSession, sessionsSorted, stepProgressPercent,
      createSession: createSession_, selectSession, deleteSession: deleteSession_,

      // 设置
      settings, searchConfigured, automationReady, openSystemSettings,

      // 表单
      isFormValid, submitStep1, stopAutomation, retryFailedStep,

      // 竞品
      startAutoResearch,

      // 右面板
      openArtifact, closeArtifact, copyArtifactContent, saveArtifactEdit,

      // 图片上传
      triggerUpload, handleFileSelect, handleDrop,

      // 竞品换品
      competitorUrl, competitorImages, localReplacementImages, activeReplacementImages,
      replacementOutputDirectory,
      ownProductImage, ownProductFileInput,
      brandHandlingMode, ownBrandName, competitorBrandAliases, brandDesignNotes, ownBrandLogo, ownBrandLogoFileInput,
      replacementFilter, replacementFilters, filteredCompetitorImages,
      selectedReplacementCount, allVisibleSelected, canGenerateReplacement,
      browserBusy, collecting, replacementRunning, replacementProgress,
      replacementMessage, replacementError,
      openCompetitorBrowser, collectCompetitorImages,
      handleLocalReplacementSelect, handleLocalReplacementFolderSelect, handleLocalReplacementDrop,
      removeLocalReplacementImage, clearLocalReplacementImages,
      selectReplacementOutputDirectory,
      handleOwnProductSelect, handleOwnProductDrop, toggleVisibleSelection,
      handleOwnBrandLogoSelect,
      kindLabel, generateSelectedReplacements, stopReplacement, downloadReplacement,

      // 聊天
      sendUserMessage,

      // 提示词卡片
      updatePromptCardText, copyPromptCard, generateImageFromCard, downloadGeneratedImage,

      // 工具
      getPreview, formatMarkdown, autoResize,
      canAccessStep, goToStep
    };
  }
});

app.mount('#app');
