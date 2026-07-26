# RayNeo 九点标定程序

这是独立 Android App，不依赖化小北学生版，也不会修改学生版标定逻辑。

## 靶纸

优先打印：

```text
board/calibration_board_a3.svg
```

- A3 横向，按 `100% / 实际尺寸` 打印，禁止“适合页面”缩放。
- 水平点距 160 mm，垂直点距 110 mm。
- 将纸固定在平整墙面，建议眼镜距纸 70-80 cm。
- 标定时纸面尽量正对使用者，并保证相机始终能看到完整 9 个十字。
- 实际叠加通常在哪个距离使用，就尽量在相近距离标定。单个 H 矩阵不能消除深度变化带来的视差。

普通 A4 打印机会缩小靶纸，不建议用打印机自动缩放 A3 文件。没有 A3 条件时，可将靶纸分块打印后无缝拼接。

## 标定步骤

1. 打开 App，允许相机权限。
2. 单击开始。
3. 左眼出现第一个青色十字，右眼保持空白。
4. 移动头部，让眼镜十字与纸上对应位置的十字重合，然后单击眼镜腿。
5. 依次完成左眼 9 点，再完成右眼 9 点。
6. 双击随时退出；完成页单击可重新标定。

每次拍照必须完整看到纸上 9 个十字。程序自动按左上到右下排序，并取当前序号对应的相机像素点。

## 输出

标定结果保存在 App 私有外部目录：

```text
Android/data/com.example.rayneocalibration/files/Documents/Calibration/calibration.json
```

文件包含：

- `left.homography`：左眼，相机归一化坐标到左眼屏幕归一化坐标。
- `right.homography`：右眼，相机归一化坐标到右眼屏幕归一化坐标。
- 每个眼睛的误差、内点数和 9 组原始对应点。

## 手动九点重算

如果怀疑自动十字检测错了，可以用电脑端手动重算 H。

优先使用眼镜标定 App 采集出来的 18 张图。先从眼镜拉取：

```powershell
D:\Android\SDK\platform-tools\adb.exe pull `
  /sdcard/Android/data/com.example.rayneocalibration/files/Pictures/CalibrationCaptures `
  D:\tmp\CalibrationCaptures
```

然后逐张手动点对应十字：

```powershell
cd D:\chemistry\化小北实验测试数据\chemistry
python Calibration\manual_calibrate.py --capture-dir D:\tmp\CalibrationCaptures
```

工具会按文件名读取：

```text
left_1_*.jpg ... left_9_*.jpg
right_1_*.jpg ... right_9_*.jpg
```

每张图只点一个十字，点当前屏幕提示的那个纸上十字。

也可以用单张包含 9 个十字的照片做快速验证：

```powershell
cd D:\chemistry\化小北实验测试数据\chemistry
python Calibration\manual_calibrate.py --image D:\path\to\calibration_photo.jpg
```

点击顺序固定为：

```text
左上、上中、右上、左中、中心、右中、左下、下中、右下
```

操作：

- 鼠标左键：标一个点。
- Backspace：撤销上一个点。
- Enter：9 个点全部标完后生成结果。
- Esc：退出。

输出会保存到：

```text
Calibration\manual_capture_points.json
Calibration\calibration_manual.json
```

`calibration_manual.json` 的结构和眼镜端 `calibration.json` 一致。`--capture-dir` 模式会分别生成 left/right 两套 H。`--image` 模式只用于快速验证，会用同一组九点同时写入 left/right。

## 构建与安装

```powershell
cd D:\chemistry\化小北实验测试数据\chemistry\Calibration\android
$env:JAVA_HOME='D:\Android\studio\jbr'
.\gradlew.bat :app:assembleDebug

D:\Android\SDK\platform-tools\adb.exe install -r `
  app\build\outputs\apk\debug\app-debug.apk
```

安装后应用名为 `眼镜九点标定`，包名为：

```text
com.example.rayneocalibration
```
