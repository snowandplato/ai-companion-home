// 独特主题色图标设计系统
// 紫色主题：#D3D1DE (#716E85 用于深色), 橙色主题：#E98E6B

interface IconProps {
  name: keyof typeof icons
  size?: number
  color?: string
  style?: React.CSSProperties
}

export const icons: Record<string, string> = {
  // ========== 导航类图标 ==========
  // 汉堡菜单 - 简约三横线设计
  menu: `<rect x="4" y="6" width="16" height="2" rx="1"/><rect x="4" y="11" width="16" height="2" rx="1"/><rect x="4" y="16" width="16" height="2" rx="1"/>`,

  // 地图 - 极简世界地图轮廓
  map: `<circle cx="12" cy="12" r="9" stroke-dasharray="3 2"/><path d="M5 12h14"/><path d="M12 5c0 0 3 3 0 7s-3 7 0 7"/><path d="M12 5c0 0-3 3 0 7s3 7 0 7"/>`,

  // ========== 底部导航 ==========
  // 对话 - 气泡风格，加重点缀
  chat: `<rect x="4" y="6" width="14" height="12" rx="4"/><circle cx="12" cy="12" r="2"/><path d="M18 14l2 2v-6"/>`,

  // 阅读 - 简约书本，翻页效果
  book: `<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M12 5c0 0 4 1 4 4v10c0 0-4 0-4-4 0 0-4 0-4 4V9c0 0 4-1 4-4Z"/><line x1="12" y1="9" x2="12" y2="19"/>`,

  // 笔记 - 便签样式
  image: `<rect x="5" y="5" width="14" height="14" rx="2"/><line x1="9" y1="9" x2="11" y2="9"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="16" x2="13" y2="16"/>`,

  // 我们 - 双心形设计，连接感
  heart: `<path d="M7 16c-3-3-3-6 0-8 1.5-1.5 4-1.5 5.5 0 .5.5.5.5 0 0 1.5-1.5 4-1.5 5.5 0 3 2 3 5 0 8Z"/><path d="M17 14a3 3 0 0 0-4.2-4.2"/><circle cx="17" cy="9" r="2"/>`,

  // ========== 功能标签 ==========
  // 思考 - 神经网络风格
  brain: `<circle cx="9" cy="12" r="2"/><circle cx="15" cy="9" r="2"/><circle cx="15" cy="15" r="2"/><path d="M11 12h2"/><path d="M13 9l2 0"/><path d="M13 15l2 0"/><circle cx="12" cy="12" r="8" stroke-dasharray="2 3"/>`,

  // 联网 - 地球加箭头
  globe: `<circle cx="12" cy="12" r="8"/><path d="M4 12h16"/><path d="M12 4c0 0 2 4 2 8s-2 8-2 8M12 4c0 0-2 4-2 8s2 8 2 8"/><path d="M18 6l2-2M6 6L4 4"/>`,

  // ========== 设置与主题 ==========
  // 设置 - 齿轮简化版
  settings: `<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M20 12h-4M8 12H4"/><circle cx="12" cy="12" r="6" stroke-dasharray="1.5"/>`,

  // 太阳 - 光芒简化
  sun: `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>`,

  // 月亮 - 新月设计
  moon: `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/><circle cx="17" cy="8" r="1.5" opacity="0.5"/>`,

  // ========== 输入区域 ==========
  // 加号 - 十字风格
  plus: `<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>`,

  // 发送 - 纸飞机
  send: `<path d="M22 2L11 13M22 2l-7 20-4-7-7-4 18-9"/>`,

  // ========== 其他 ==========
  // 向下箭头
  arrowDown: `<path d="M12 5v14M19 12l-7 7-7-7"/>`,

  // 关闭
  close: `<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>`,

  // 眼睛 - 显示密码
  eye: `<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>`,

  // 眼睛-隐藏密码
  eyeOff: `<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7c.41 0 .82 0 1.23-.08"/><path d="M2 2l20 20"/>`,

  // 停止 - 方形停止按钮
  stop: `<rect x="6" y="6" width="12" height="12" rx="2"/>`,

  // 编辑 - 笔形
  edit: `<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>`
}

export const Icon: React.FC<IconProps> = ({ name, size = 24, color, style }) => {
  const iconSvg = icons[name]

  if (!iconSvg) {
    console.warn(`Icon "${name}" not found`)
    return null
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color || 'currentColor'}
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ ...style }}
    >
      <g dangerouslySetInnerHTML={{ __html: iconSvg }} />
    </svg>
  )
}
