/**
 * 遮罩绘制工具类型定义
 * 用于去水印、替换背景、智能合成等场景的画笔绘制功能
 */

/** 遮罩笔划中的单个点坐标 */
export interface MaskPoint {
  x: number;
  y: number;
}

/** 单条遮罩笔划（支持画笔/橡皮擦，记录完整绘制参数） */
export interface MaskStroke {
  /** 唯一标识 */
  id: string;
  /** 扁平化存储 [x1, y1, x2, y2, ...]，减少内存占用 */
  points: number[];
  /** 画笔/橡皮擦大小（像素） */
  size: number;
  /** 硬度/羽化 0-100（0=完全羽化，100=硬边） */
  hardness: number;
  /** 笔尖形状 */
  shape: 'circle' | 'square';
  /** 工具类型 */
  tool: 'brush' | 'eraser';
  /** 时间戳（用于撤销排序） */
  timestamp: number;
}

/** 画笔/橡皮擦设置 */
export interface BrushSettings {
  /** 画笔/橡皮擦大小（1-100px） */
  size: number;
  /** 硬度/羽化程度（0-100） */
  hardness: number;
  /** 笔尖形状 */
  shape: 'circle' | 'square';
  /** 当前工具 */
  tool: 'brush' | 'eraser' | 'magicWand';
}

/** 遮罩导出选项 */
export interface MaskExportOptions {
  /** 导出格式 */
  format: 'png' | 'jpeg';
  /** 图像质量（0-1，仅 jpeg 有效） */
  quality: number;
  /** 最大尺寸（默认 2048，超过则缩小） */
  maxDimension: number;
}

/** 遮罩绘制工具类型 */
export type MaskTool = 'rectangle' | 'brush';

/** 默认画笔设置 */
export const DEFAULT_BRUSH_SETTINGS: BrushSettings = {
  size: 30,
  hardness: 80,
  shape: 'circle',
  tool: 'brush',
};

/** 最大撤销步数 */
export const MAX_UNDO_STEPS = 20;

/** 点采样距离阈值（画笔大小的 1/3） */
export const POINT_SAMPLE_RATIO = 3;
