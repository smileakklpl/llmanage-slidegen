export type Locale = "zh" | "en";

export const translations = {
  zh: {
    // Navbar
    appName: "智匯數據簡報神器",
    langToggle: "EN",

    // GeneratePage
    heroTitle: "智匯數據簡報神器",
    heroSubtitle: "上傳 Excel 資料，AI 幫你生成精美簡報",

    // GenerateForm
    fileLabel: "Excel 檔案（可多次選擇）",
    fileError: "僅接受 .xlsx 格式檔案",
    fileRequired: "請至少選擇一個檔案",
    fileSelected: "已選擇 {count} 個檔案",
    fileSelectButton: "選擇檔案",
    promptLabel: "提示詞",
    promptPlaceholder: "請描述你想要生成的簡報內容…",
    promptRequired: "請輸入提示詞",
    submitButton: "開始生成",
    submitting: "處理中…",

    // HealthStatus
    healthOk: "後端連線正常",
    healthError: "無法連線後端",
    healthChecking: "檢查連線中…",

    // JobPage
    jobTitle: "工作狀態",
    jobLoading: "載入中…",
    jobError: "無法取得工作狀態",
    jobSucceeded: "簡報生成完成",
    aiResponseTitle: "AI 分析回覆",
    newTask: "← 建立新任務",

    // JobProgress
    progressLabel: "進度",

    // ArtifactList
    artifactTitle: "生成結果",
    artifactWarning: "⚠️ 開發階段模擬結果",
    artifactDownload: "下載",

    // SendEmailSection
    sendTitle: "寄送簡報",
    senderLabel: "寄件者",
    senderPlaceholder: "你的 Email",
    senderRequired: "請填寫寄件者 Email",
    senderInvalid: "寄件者 Email 格式不正確",
    recipientLabel: "收件者",
    recipientPlaceholder: "輸入 Email 後按 Enter 加入",
    recipientAdd: "加入",
    recipientInvalid: "請輸入有效的電子郵件地址",
    recipientDuplicate: "此收件者已加入",
    recipientRequired: "請至少加入一位收件者",
    subjectLabel: "主旨",
    subjectPlaceholder: "郵件主旨",
    bodyLabel: "內容",
    bodyPlaceholder: "在此撰寫郵件內容…",
    attachmentLabel: "附件",
    uploadExtra: "上傳其他附件",
    sendButton: "寄送",
    sending: "寄送中…",
    sendFailed: "寄送失敗",

    // ErrorMessage
    retryButton: "重試",

    // NotFoundPage
    notFoundTitle: "404",
    notFoundMessage: "找不到此頁面",
    notFoundBack: "回到首頁",

    // General
    unknownError: "發生未知錯誤",
    remove: "移除",
  },
  en: {
    // Navbar
    appName: "Smart Deck",
    langToggle: "中",

    // GeneratePage
    heroTitle: "Smart Deck",
    heroSubtitle: "Upload Excel data, let AI generate beautiful presentations",

    // GenerateForm
    fileLabel: "Excel Files (select multiple)",
    fileError: "Only .xlsx files are accepted",
    fileRequired: "Please select at least one file",
    fileSelected: "{count} file(s) selected",
    fileSelectButton: "Choose Files",
    promptLabel: "Prompt",
    promptPlaceholder: "Describe the presentation you want to generate…",
    promptRequired: "Please enter a prompt",
    submitButton: "Generate",
    submitting: "Processing…",

    // HealthStatus
    healthOk: "Backend connected",
    healthError: "Cannot connect to backend",
    healthChecking: "Checking connection…",

    // JobPage
    jobTitle: "Job Status",
    jobLoading: "Loading…",
    jobError: "Cannot retrieve job status",
    jobSucceeded: "Presentation generated successfully",
    aiResponseTitle: "AI Response",
    newTask: "← Create New Task",

    // JobProgress
    progressLabel: "Progress",

    // ArtifactList
    artifactTitle: "Results",
    artifactWarning: "⚠️ Development stage mock results",
    artifactDownload: "Download",

    // SendEmailSection
    sendTitle: "Send Presentation",
    senderLabel: "From",
    senderPlaceholder: "Your email",
    senderRequired: "Please enter your email",
    senderInvalid: "Invalid sender email format",
    recipientLabel: "To",
    recipientPlaceholder: "Enter email and press Enter to add",
    recipientAdd: "Add",
    recipientInvalid: "Please enter a valid email address",
    recipientDuplicate: "This recipient is already added",
    recipientRequired: "Please add at least one recipient",
    subjectLabel: "Subject",
    subjectPlaceholder: "Email subject",
    bodyLabel: "Body",
    bodyPlaceholder: "Write your email content here…",
    attachmentLabel: "Attachments",
    uploadExtra: "Upload other files",
    sendButton: "Send",
    sending: "Sending…",
    sendFailed: "Failed to send",

    // ErrorMessage
    retryButton: "Retry",

    // NotFoundPage
    notFoundTitle: "404",
    notFoundMessage: "Page not found",
    notFoundBack: "Back to Home",

    // General
    unknownError: "An unknown error occurred",
    remove: "Remove",
  },
} as const;

export type TranslationKey = keyof (typeof translations)["zh"];
