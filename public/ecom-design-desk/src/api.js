// OutoCut 集成版 API 调用层。密钥只保存在本地引擎的加密设置中，
// 本页面只持有桌面端启动时生成的本地鉴权令牌。
(function () {
  const params = new URLSearchParams(window.location.search);
  const baseUrl = (params.get('engineBase') || 'http://127.0.0.1:35006').replace(/\/$/, '');
  const token = params.get('token') || 'development-token';

  async function request(path, body, options) {
    options = options || {};
    let response;
    try {
      response = await fetch(baseUrl + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {
          Authorization: 'Bearer ' + token,
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' })
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: options.signal
      });
    } catch (error) {
      if (error && error.name === 'AbortError') throw error;
      throw new Error('无法连接 OutoCut 本地引擎：' + (error.message || String(error)));
    }
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.message || data.detail || ('请求失败 (' + response.status + ')'));
    return data;
  }

  const ApiClient = {
    async getConfiguration() {
      return request('/ecom-design/configuration');
    },

    async callTextLLM(prompt, systemPrompt, options) {
      options = options || {};
      const data = await request('/ecom-design/text', {
        workflow_id: options.workflowId || '',
        prompt: prompt,
        system_prompt: systemPrompt || '',
        max_tokens: options.maxTokens || 4096,
        temperature: options.temperature === undefined ? 0.7 : options.temperature,
        timeout_seconds: options.timeoutSeconds || null
      }, { signal: options.signal });
      return data.text;
    },

    async callImageGeneration(prompt, imageBase64, options) {
      options = options || {};
      const data = await request('/ecom-design/image', {
        workflow_id: options.workflowId || '',
        prompt: prompt,
        image_base64: imageBase64 || null,
        size: options.size || '1024x1024',
        quality: options.quality || 'high',
        output_format: options.outputFormat || 'png'
      }, { signal: options.signal });
      return data.image_url;
    },

    async callProductReplacement(templateImageBase64, productImageBase64, options) {
      options = options || {};
      const data = await request('/ecom-design/image', {
        workflow_id: options.workflowId || '',
        prompt: options.prompt || [
          'Image 1 is the competitor layout/template. Image 2 is our replacement product.',
          'Replace only the product object in Image 1 with the exact product from Image 2.',
          'Preserve every other element from Image 1: canvas size, crop, camera angle, composition, background, lighting, shadows, props, people, typography, copy, labels and decorations.',
          'Do not copy any background, text or props from Image 2. Do not redesign, translate, add, remove or rewrite anything except the product itself.',
          'Match the replacement product to the original object position, scale, perspective, contact shadow and scene lighting. Return one finished image.'
        ].join('\n'),
        image_base64: options.brandImageBase64 || null,
        template_image_base64: templateImageBase64,
        product_image_base64: productImageBase64,
        size: options.size || 'auto',
        quality: options.quality || 'high',
        output_format: options.outputFormat || 'png'
      }, { signal: options.signal });
      return data.image_url;
    },

    async callTavilySearch(query, options) {
      options = options || {};
      return request('/ecom-design/search', {
        workflow_id: options.workflowId || '',
        query: query,
        search_depth: options.searchDepth || 'advanced',
        max_results: options.maxResults || 10
      }, { signal: options.signal });
    },

    async cancelWorkflow(workflowId) {
      if (!workflowId) return { cancelled: 0 };
      return request('/ecom-design/cancel', { workflow_id: workflowId });
    },

    async testLLMConnection() {
      return this.callTextLLM('你好，请回复“连接成功”', '', { maxTokens: 50 });
    },

    async testImageConnection() {
      return this.callImageGeneration('A simple blue circle on white background', null, { size: '1024x1024' });
    }
  };

  window.ApiClient = ApiClient;
})();
