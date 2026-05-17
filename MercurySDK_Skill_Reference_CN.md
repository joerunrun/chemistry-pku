# MercurySDK API 参考文档（中文）

本文档描述 **MercurySDK** 在 Android 应用中的公开 API，包含 Demo App 中已实际调用的接口，适用于在 RayNeo X3 AR 眼镜平台上开发双目合目（镜像显示）UI 与触控交互。

---

## 目录

1. [概述](#1-概述)
2. [快速开始](#2-快速开始)
3. [核心 API](#3-核心-api)
4. [双目合目与镜像显示 UI](#4-双目合目与镜像显示-ui)
5. [焦点与触控](#5-焦点与触控)
6. [对话框与 Toast](#6-对话框与-toast)
7. [RecyclerView 支持](#7-recyclerview-支持)
8. [扩展与工具](#8-扩展与工具)
9. [全局约束总表（Checklist）](#9-全局约束总表checklist)

## 1. 概述

MercurySDK 为 RayNeo AR 眼镜提供：

- **双目合目布局**：左右眼视图对称、同步更新，并可施加 3D 视差效果。
- **镜腿触控**：将镜腿触摸事件映射为统一的手势（单击、双击、滑动等），便于在无触摸屏设备上操作。
- **焦点管理**：在镜腿滑动/点击下切换焦点、处理列表与弹窗内的焦点与跟踪。

文档中的 **包名** 均以 `com.ffalcon.mercury.android.sdk` 为根包。

### 1.1 类与函数签名摘要（Reference 索引）

下表用于快速定位常用类型与入口，风格参考 Android 官方 Reference 的“类概览 + 核心签名”。

| 类型                           | 包路径                  | 核心签名（节选）                                              | 作用              |
|------------------------------|----------------------|-------------------------------------------------------|-----------------|
| `MercurySDK`                 | `...sdk`             | `init(application: Application)`                      | SDK 初始化入口。      |
| `MobileState`                | `...sdk.api`         | `isMobileConnected(): Flow<Boolean>`                  | 手机连接状态监听。       |
| `BindingPair<B>`             | `...sdk.core`        | `updateView { }` / `setLeft { }` / `checkIsLeft(...)` | 左右布局映射与同步更新。    |
| `make3DEffect`               | `...sdk.core`        | `make3DEffect(leftView, rightView, enable, parallax)` | 双侧 3D 视差设置。     |
| `make3DEffectForSide`        | `...sdk.core`        | `make3DEffectForSide(view, isLeft, enable, parallax)` | 单侧 3D 视差设置。     |
| `BaseMirrorActivity<B>`      | `...sdk.ui.activity` | `abstract class BaseMirrorActivity<B : ViewBinding>`  | Activity 级合目基类。 |
| `FToast`                     | `...sdk.ui.toast`    | `show(...)` / `showCustom(...)`                       | 合目 Toast。       |
| `FDialog.Builder<T>`         | `...sdk.ui.dialog`   | `setContentView(...)` / `setEventHandler(...)`        | 合目 Dialog 构建器。  |
| `TempleAction`               | `...sdk.touch`       | `sealed class TempleAction`                           | 手势语义模型。         |
| `TempleActionViewModel`      | `...sdk.touch`       | `state: SharedFlow<TempleAction>`                     | 手势事件流分发。        |
| `FocusHolder` / `FocusInfo`  | `...sdk.ui.util`     | `addFocusTarget(...)` / `currentFocus(...)`           | 通用焦点项管理。        |
| `FixPosFocusTracker`         | `...sdk.ui.util`     | `handleFocusTargetEvent(action)`                      | 固定焦点项切换逻辑。      |
| `RecyclerViewFocusTracker`   | `...sdk.ui.util`     | `handleActionEvent(it, block)`                        | 移动焦点项列表跟踪。      |
| `RecyclerViewSlidingTracker` | `...sdk.ui.util`     | `observeOriginMotionEventStream(...)`                 | 固定焦点项 + 跟手滚动。   |
| `StartSnapHelper`            | `...sdk.util`        | `StartSnapHelper(offset2Start)`                       | 列表起始吸附。         |
| `DeviceUtil`                 | `...sdk.util`        | `isX3Device(): Boolean`                               | X3 设备判断。        |
| `FLogger`                    | `...sdk.util`        | `d(...)` / `i(...)` / `e(...)`                        | SDK 统一日志。       |

---

## 2. 快速开始

### 2.1 接入前置条件（来自 RayNeo X3 ARSDK 文档）

为保证本文档中的 API 可正常工作，建议先满足以下基础接入项：

1. 打开 ViewBinding（SDK 基于 ViewBinding 封装）：

```groovy
buildFeatures {
    viewBinding = true
}
```

2. 在主模块 `AndroidManifest.xml` 的 `application` 中增加：

```xml
<meta-data
    android:name="com.rayneo.mercury.app"
    android:value="true" />
```

不配置该项时，应用可能无法在眼镜 Launcher 中显示。

### 2.2 初始化 SDK

在 `Application#onCreate` 中调用一次初始化：

```kotlin
// Application 子类中
override fun onCreate() {
    super.onCreate()
    MercurySDK.init(this)
}
```

**类**：`com.ffalcon.mercury.android.sdk.MercurySDK`

| 方法                               | 说明                                          |
|----------------------------------|---------------------------------------------|
| `init(application: Application)` | 使用当前 Application 初始化 SDK，必须在使用其他 SDK 能力前调用。 |
| `mApplication: Application`      | 只读。初始化后可通过此处获取 Application 实例。              |

**注意事项（Since / Threading / Lifecycle）**

- **Since**：当前仓库对应的 MercurySDK 版本线（`v0.2.4` 系列）。
- **Threading**：`init()` 建议仅在主线程、应用启动阶段调用一次。
- **Lifecycle**：建议在 `Application#onCreate` 完成初始化；重复初始化不会报错，但不建议在运行期频繁调用。

### 2.3 建议的页面交互约定（来自 RayNeo X3 ARSDK 文档）

- 焦点切换：通常使用前滑/后滑。
- 触发当前焦点行为：通常使用单击。
- 页面返回：推荐使用双击退出当前页（Demo 与 SDK 示例一致）。

---

## 3. 核心 API

### 3.1 MobileState

**包**：`com.ffalcon.mercury.android.sdk.api`

用于观察与雷鸟 AR 手机 App 的连接状态。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：`MobileState` 在 X3 能力集成中提供连接状态观察能力。
- **Threading**：`Flow` 可在任意协程上下文收集；UI 更新请切回主线程（或在 `lifecycleScope` 主线程环境中处理）。
- **Lifecycle**：内部在 `onStart` 注册 `ContentObserver`、在 `onCompletion` 自动反注册；请避免在无生命周期约束的全局协程中长期悬挂收集。

| 成员                            | 类型              | 说明                                            |
|-------------------------------|-----------------|-----------------------------------------------|
| `METHOD_MOBILE_CONNECT_STATE` | `String`        | ContentProvider 方法名常量：`"mobileConnectState"`。 |
| `isMobileConnected()`         | `Flow<Boolean>` | 返回当前手机连接状态的 Flow，连接变化时自动推送。                   |

**示例：根据 BLE 连接状态更新 UI**

```kotlin
MobileState.isMobileConnected()
    .onEach { connected ->
        mBindingPair.updateView {
            tvBleStatus.text = if (connected) "connect" else "disconnect"
        }
    }
    .launchIn(lifecycleScope)
```

---

### 3.2 DeviceUtil

**包**：`com.ffalcon.mercury.android.sdk.util`

设备型号判断工具。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：X3 适配阶段新增，用于区分 X2/X3 分支逻辑。
- **Threading**：`isX3Device()` 为轻量同步调用，可在任意线程调用。
- **Lifecycle**：无组件生命周期绑定，推荐在页面初始化阶段读取并缓存用于 UI 分支。

| 方法                      | 说明                 |
|-------------------------|--------------------|
| `isX3Device(): Boolean` | 当前设备是否为 RayNeo X3。 |

**示例**

```kotlin
if (DeviceUtil.isX3Device()) {
    tvDevicesType.text = "RayNeo X3"
    // 仅 X3 显示 BLE 状态等
} else {
    tvDevicesType.text = "RayNeo X2"
}
```

---

## 4. 双目合目与镜像显示 UI

### 4.1 BindingPair&lt;B : ViewBinding&gt;

**包**：`com.ffalcon.mercury.android.sdk.core`

基于 ViewBinding 的左右布局对，对左侧布局的操作可同步映射到右侧，用于双目合目页面。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：作为合目渲染基础能力，贯穿 Activity/Fragment/View 级组件。
- **Threading**：`update/updateView/setLeft` 属于 UI 操作，应在主线程执行。
- **Lifecycle**：`BindingPair` 与其持有的左右 Binding 同生命周期；页面销毁后不要继续引用内部 View。

| 成员         | 类型      | 说明                  |
|------------|---------|---------------------|
| `left`     | `B`     | 左侧 ViewBinding。     |
| `right`    | `B`     | 右侧 ViewBinding。     |
| `PARALLAX` | `Float` | 默认视差值（3f），用于 3D 效果。 |

**方法**

| 方法                                                   | 说明                                               |
|------------------------------------------------------|--------------------------------------------------|
| `update(block: T.() -> Unit)`                        | 对 `left`、`right` 均执行 `block`，用于同步更新左右视图。         |
| `updateView(block: T.() -> Unit)`                    | 同 `update`，语义上强调“更新视图”。                          |
| `setLeft(block: T.() -> Unit)`                       | 仅在左侧执行 `block`（如只注册左侧事件）。                        |
| `checkIsLeft(t: T): Boolean`                         | 判断当前 ViewBinding 是否为左侧。                          |
| `enable3DEffect(vararg leftViews, enable, parallax)` | 对一组左侧 View 批量开启/关闭 3D 视差；仅传入**左侧** View，否则可能空指针。 |

**注意**：`enable3DEffect` 不要在 `updateView` / `update` 的 block 内对“右侧 View”调用，内部用左侧 View 作 key 查找右侧，传右 View 会得到 null。

---

### 4.2 make3DEffect（顶层函数）

**包**：`com.ffalcon.mercury.android.sdk.core`

为一对左右 View 设置或取消 3D 视差（水平平移）。

```kotlin
@JvmOverloads
fun make3DEffect(
    leftView: View,
    rightView: View,
    enable: Boolean = true,
    parallax: Float = BindingPair.PARALLAX
)
```

| 参数          | 说明                         |
|-------------|----------------------------|
| `leftView`  | 左眼对应 View。                 |
| `rightView` | 右眼对应 View。                 |
| `enable`    | `true` 施加视差，`false` 恢复为 0。 |
| `parallax`  | 偏移量：左 View 向右移、右 View 向左移。 |

---

### 4.3 make3DEffectForSide（顶层函数）

**包**：`com.ffalcon.mercury.android.sdk.core`

对单侧（左或右）的一个 View 设置 3D 视差，常用于列表项、按钮等单侧视图。

```kotlin
@JvmOverloads
fun make3DEffectForSide(
    view: View,
    isLeft: Boolean,
    enable: Boolean = true,
    parallax: Float = BindingPair.PARALLAX
)
```

| 参数         | 说明                      |
|------------|-------------------------|
| `view`     | 要设置视差的 View。            |
| `isLeft`   | `true` 表示该 View 属于左侧布局。 |
| `enable`   | 是否启用视差。                 |
| `parallax` | 偏移量。                    |

**示例：列表项或按钮的焦点高亮 + 3D**

```kotlin
pair.updateView {
    val isLeft = pair.checkIsLeft(this)
    triggerFocus(hasFocus, btnOk, isLeft)  // 例如改背景色
}
// 在自定义 triggerFocus 内：
make3DEffectForSide(view, isLeft, hasFocus)
```

---

### 4.4 ViewPair&lt;T : View&gt;

**包**：`com.ffalcon.mercury.android.sdk.core`

对左右两个同类型 View 的封装，继承自 `BaseMirrorAction<T>`，提供 `update`、`setLeft`、`checkIsLeft` 等，常用于左右两个 RecyclerView 的同步操作。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于通用左右 View 成对操作场景。
- **Threading**：涉及 View 更新时应在主线程调用。
- **Lifecycle**：持有 View 强引用，建议仅在页面存活期内持有，避免在静态单例中缓存。

**构造**

```kotlin
ViewPair(mBindingPair.left.recyclerView, mBindingPair.right.recyclerView)
```

---

### 4.5 BaseMirrorActivity&lt;B : ViewBinding&gt;

**包**：`com.ffalcon.mercury.android.sdk.ui.activity`

带左右镜像布局的 Activity 基类：自动根据泛型 `B` 生成左右两份 Binding，并放入水平均分的根布局；同时继承镜腿触控与手势分发（见 [BaseEventActivity / 触控](#5-焦点与触控)）。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：合目 Activity 的标准基类。
- **Threading**：手势收集与 UI 更新按 Android 组件约定在主线程执行。
- **Lifecycle**：建议在 `onCreate` 做 UI 初始化，在 `onStart/onResume` 开始收集事件，在 `onStop/onDestroy` 释放播放器/传感器等资源。

| 成员                      | 类型                      | 说明                                                                 |
|-------------------------|-------------------------|--------------------------------------------------------------------|
| `mBindingPair`          | `BindingPair<B>`        | 左右 ViewBinding 对，用于 `updateView`、`enable3DEffect`、`checkIsLeft` 等。 |
| `templeActionViewModel` | `TempleActionViewModel` | 镜腿手势 ViewModel，通过 `state` Flow 收集手势。                               |

**使用方式**

1. 继承并指定 ViewBinding 类型，例如 `BaseMirrorActivity<ActivityApiBinding>()`。
2. 在 `onCreate` 中通过 `mBindingPair.updateView { ... }` 更新左右 UI。
3. 在 `lifecycleScope` 中 `templeActionViewModel.state.collect { ... }` 处理单击、双击、滑动等。

**注意**：根布局会拦截触摸事件并交给 Activity 的 `onTouchEvent`，以保证镜腿手势能统一处理。

---

### 4.6 MirrorContainerView

**包**：`com.ffalcon.mercury.android.sdk.ui.wiget`

非抽象、可直接使用的左右镜像容器 View（LinearLayout 水平排列左右两栏）。不依赖泛型，通过 `bindTo` 动态绑定 ViewBinding 类型。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于快速接入 View 级合目场景。
- **Threading**：`bindTo()` 及后续 View 更新应在主线程执行。
- **Lifecycle**：`bindTo()` 会向容器添加左右子树，通常建议每个实例调用一次；重复调用需自行避免重复叠加。

| 方法                                             | 说明                                                   |
|------------------------------------------------|------------------------------------------------------|
| `bindTo(bindingClz: Class<B>): BindingPair<B>` | 使用指定 ViewBinding 类生成左右两个布局并加入容器，返回 `BindingPair<B>`。 |

**示例（如 FToast、FDialog 内部）**

```kotlin
val toastView = MirrorContainerView(context).apply {
    val pair = bindTo(FfalconToastBinding::class.java)
    pair.updateView { tvToast.text = msg }
    make3DEffect(left.tvToast, right.tvToast, true, 15f)
}
```

---

### 4.7 BaseMirrorContainerView<B : ViewBinding>

**包**：`com.ffalcon.mercury.android.sdk.ui.wiget`

支持继承的镜像容器基类，通过泛型指定 ViewBinding，子类实现 `onInit()` 完成初始化。内部持有 `mBindingPair: BindingPair<B>`，用法与 `BaseMirrorActivity` 中的 `mBindingPair` 类似。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于需要自定义 View 继承体系的合目容器。
- **Threading**：初始化与视图更新均应在主线程。
- **Lifecycle**：初始化在构造阶段触发，避免在构造早期访问尚未准备好的外部依赖（如未附着 Window 前的尺寸信息）。

---

### 4.8 BaseMirrorFragment<B, H>

**包**：`com.ffalcon.mercury.android.sdk.ui.fragment`

Fragment 级别的镜像基类，使用 `HolderPair<B, H>` 和 `BindingPair<B>` 管理左右 Holder 与 Binding。适合在 Fragment 内做左右对称 UI 与事件同步，用法与 Activity 镜像类似。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于 Fragment 粒度的合目封装。
- **Threading**：Fragment UI 回调与 `mBindingPair` 更新应在主线程。
- **Lifecycle**：推荐遵循 `onCreateView` 创建、`onDestroyView` 释放 View 引用的模式，避免越过 View 生命周期访问 Binding。

---

## 5. 焦点与触控

### 5.1 IFocusable

**包**：`com.ffalcon.mercury.android.sdk.focus`

焦点能力接口，用于自定义焦点对象（如 FixPosFocusTracker 的 `focusObj`、RecyclerView 的 tracker 等）。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：统一焦点系统的抽象接口。
- **Threading**：`hasFocus` 的读写建议在主线程，保证与 UI 状态一致。
- **Lifecycle**：`focusParent` 建议指向当前页面可达对象；页面销毁时应断开跨页面焦点引用链。

| 属性            | 类型            | 说明                    |
|---------------|---------------|-----------------------|
| `hasFocus`    | `Boolean`     | 当前是否拥有焦点。             |
| `focusParent` | `IFocusable?` | 父焦点对象；释放焦点时可把焦点还给父对象。 |

---

### 5.2 reqFocus / releaseFocus（扩展函数）

**包**：`com.ffalcon.mercury.android.sdk.focus`

```kotlin
fun IFocusable.reqFocus(parent: IFocusable? = null)
fun IFocusable.releaseFocus()
```

- `reqFocus(parent)`：将当前对象设为有焦点；若传 `parent`，会设置 `focusParent`。
- `releaseFocus()`：取消当前焦点，并调用 `focusParent?.reqFocus()` 把焦点交回父对象。

---

### 5.3 FocusHolder

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

固定顺序的焦点列表管理器：维护一组 `FocusInfo`，支持 `next()` / `previous()` 切换焦点，并可循环。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：焦点列表切换的核心管理类。
- **Threading**：内部状态非线程安全，建议只在主线程调用。
- **Lifecycle**：列表项通常与页面 View 生命周期一致；动态删除项后建议同步校验当前焦点项有效性。

| 构造参数                    | 说明               |
|-------------------------|------------------|
| `loop: Boolean = false` | 是否循环切换（到头/尾后绕回）。 |

**方法**

| 方法                                                | 说明                                    |
|---------------------------------------------------|---------------------------------------|
| `addFocusTarget(vararg focusInfoList: FocusInfo)` | 添加可参与焦点切换的项。                          |
| `removeFocusTarget(target: Any)`                  | 移除与 `target` 关联的焦点项；若当前焦点正是该项则会切到下一项。 |
| `currentFocus(target: Any)`                       | 将当前焦点设为指定 `target` 对应的项（常用于设置默认焦点）。   |
| `next()`                                          | 切换到下一项。                               |
| `previous()`                                      | 切换到上一项。                               |

**属性**

| 属性                 | 类型          | 说明        |
|--------------------|-------------|-----------|
| `currentFocusItem` | `FocusInfo` | 当前获得焦点的项。 |

---

### 5.4 FocusInfo

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

表示一个可被 FocusHolder 管理的焦点项。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：焦点模型基础单元。
- **Threading**：`eventHandler` 与 `focusChangeHandler` 默认按手势分发线程触发，实际使用中建议只做主线程 UI 操作。
- **Lifecycle**：`target` 常为 View，请确保其仍附着在有效页面中再执行焦点变更。

**构造**

```kotlin
FocusInfo(
    target: Any,                              // 任意对象，用于标识（如 View）
    eventHandler: (TempleAction) -> Unit,       // 镜腿手势回调
    focusChangeHandler: (hasFocus: Boolean) -> Unit  // 焦点获得/失去时回调
)
```

| 方法               | 说明                                       |
|------------------|------------------------------------------|
| `fetchFocus()`   | 内部调用 `focusChangeHandler(true)`，表示获得焦点。  |
| `releaseFocus()` | 内部调用 `focusChangeHandler(false)`，表示失去焦点。 |

**示例**

```kotlin
FocusInfo(
    btnAdd,
    eventHandler = { action ->
        when (action) {
            is TempleAction.Click -> addDynamicFocus()
            else -> Unit
        }
    },
    focusChangeHandler = { hasFocus ->
        mBindingPair.updateView {
            triggerFocus(hasFocus, btnAdd, mBindingPair.checkIsLeft(this))
        }
    }
)
```

---

### 5.5 FixPosFocusTracker

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

“固定位置”焦点切换跟踪：根据镜腿滑动（或单步上下/左右）在 FocusHolder 的列表里切换焦点，并可把未处理的手势交给当前焦点项的 `eventHandler`。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于固定焦点项（或固定区域）的手势驱动切换。
- **Threading**：`handleFocusTargetEvent()` 需要与 UI 焦点更新同线程（推荐主线程）。
- **Lifecycle**：仅在 `focusObj.hasFocus == true` 时响应事件；页面切换或弹窗抢焦点时应及时切换该状态。

**构造**

```kotlin
FixPosFocusTracker(
    focusHolder: FocusHolder,
    continuous: Boolean = false,   // 是否按连续滑动距离切换
    isVertical: Boolean = true,    // 垂直滑动切换为 true，水平为 false
    ignoreDelta: Int = IGNORE_DELTA
)
```

| 成员                                             | 类型                              | 说明                                                                           |
|------------------------------------------------|---------------------------------|------------------------------------------------------------------------------|
| `focusHolder`                                  | `FocusHolder`                   | 管理的焦点列表。                                                                     |
| `focusObj`                                     | `IFocusable`                    | 用于表示“当前跟踪器是否拥有焦点”，可绑定到界面（如给 RecyclerView 或整页）。                               |
| `onFocusChangeListener`                        | `OnTrackerFocusChangeListener?` | 焦点变化回调。                                                                      |
| `handleFocusTargetEvent(action: TempleAction)` | 方法                              | 在获得焦点时根据 `TempleAction` 执行 `next`/`previous` 或转给当前 `FocusInfo.eventHandler`。 |

**常量**：`IGNORE_DELTA = 50`，连续滑动模式下超过该距离才触发切换。

**示例**

```kotlin
val focusHolder = FocusHolder(true)
focusHolder.addFocusTarget(focusInfo1, focusInfo2)
focusHolder.currentFocus(btnAdd)

fixPosFocusTracker = FixPosFocusTracker(focusHolder).apply {
    focusObj.hasFocus = true
}
// 在 templeActionViewModel.state 的 collect 中：
fixPosFocusTracker?.handleFocusTargetEvent(it)
```

---

### 5.6 addFocusView（BindingPair 扩展）

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

在已有镜像布局上**动态**添加一对左右焦点 View，并加入指定 `FocusHolder`，自动创建 `FocusInfo`、同步左右视图与 3D。

```kotlin
fun <T : ViewBinding, V : View> BindingPair<T>.addFocusView(
    parent: ViewGroup,
    viewFactory: () -> V,
    focusHolder: FocusHolder,
    focusConfig: FocusConfig<V>.() -> Unit = {}
): FocusViewHandle<V>
```

| 参数            | 说明                                         |
|---------------|--------------------------------------------|
| `parent`      | 左侧布局中的父容器（仅传左侧），右侧会通过 Binding 映射自动找到对应父容器。 |
| `viewFactory` | 创建单个 View 的工厂（会调用两次，分别生成左、右）。              |
| `focusHolder` | 要加入的 FocusHolder。                          |
| `focusConfig` | 对 `FocusConfig<V>` 的配置。                    |

**FocusConfig&lt;V&gt;** 可选配置：

| 属性                    | 类型                                  | 说明                                  |
|-----------------------|-------------------------------------|-------------------------------------|
| `eventHandler`        | `((TempleAction) -> Unit)?`         | 该焦点项的手势处理。                          |
| `onFocusChange`       | `((V, Boolean, Boolean) -> Unit)?`  | (view, hasFocus, isLeft) 焦点变化时更新外观。 |
| `autoRequestFocus`    | `Boolean`                           | 是否在添加后立即请求焦点。                       |
| `layoutParamsFactory` | `((ViewGroup, V) -> LayoutParams)?` | 自定义左右子 View 的 LayoutParams。         |

**返回值**：`FocusViewHandle<V>`，可后续移除或请求焦点。

---

### 5.7 FocusViewHandle<V : View>

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

由 `addFocusView` 返回，用于管理动态添加的焦点 View。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：动态焦点项能力配套句柄。
- **Threading**：`clearFocusView()/updateView()` 涉及 View 树修改，应在主线程执行。
- **Lifecycle**：页面销毁前建议清理不再使用的动态焦点项，防止遗留无效引用。

| 方法                                | 说明                                    |
|-----------------------------------|---------------------------------------|
| `clearFocusView()`                | 从父容器移除左右 View，并从 FocusHolder 中移除该焦点项。 |
| `requestFocus()`                  | 让 FocusHolder 将当前焦点设为此 View。          |
| `updateView(block: V.() -> Unit)` | 对左右两个 View 同时执行 `block`，用于同步更新。       |

---

### 5.8 TempleAction（密封类）

**包**：`com.ffalcon.mercury.android.sdk.touch`

表示镜腿触控产生的各类手势，在 `templeActionViewModel.state` 或 Dialog/RecyclerView 的回调中收到。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：BaseEventActivity 手势流统一语义模型。
- **Threading**：Action 本身是数据对象，可跨线程传递；但消费后更新 UI 需回到主线程。
- **Lifecycle**：建议结合 `repeatOnLifecycle` 收集，避免页面不可见时继续消费手势事件。

| 子类型                                           | 说明                     |
|-----------------------------------------------|------------------------|
| `Idle`                                        | 空操作。                   |
| `Click`                                       | 单击。                    |
| `LongClick`                                   | 长按。                    |
| `DoubleClick`                                 | 双击。                    |
| `TripleClick`                                 | 三击。                    |
| `SlideBackward(args)`                         | 向后滑（如右滑）。              |
| `SlideForward(args)`                          | 向前滑（如左滑）。              |
| `SlideUpwards(args)`                          | 向上滑。                   |
| `SlideDownwards(args)`                        | 向下滑。                   |
| `SlideContinuous(delta, longClick, vertical)` | 连续滑动，`delta` 为与按下点的差值。 |
| `MoveUp(isLongClick)`                         | 手指抬起。                  |
| `ActionUp`                                    | 从 DOWN 开始的事件序列结束。      |
| `ActionDown`                                  | 按下。                    |
| `DoubleFingerClick` / `DoubleFingerLongClick` | 双指点击/长按。               |

所有数据类形式的 Action 都带有 `consumed: Boolean`，可在业务中标记已消费以避免重复处理。

**滑动方向语义说明（X3 文档补充）**

`SlideForward` 与 `SlideBackward` 的实际触发方向，受系统“自然模式/非自然模式”设置影响。  
建议业务逻辑不要把该二者硬编码为固定“左/右滑”，而是按“前进/后退”语义设计交互，或在设置页做用户可配置映射。

### 5.9 CommonTouchCallback（兼容说明）

**包**：`com.ffalcon.mercury.android.sdk.touch`

App 虽未直接实现该类，但它是 `BaseTouchActivity` 事件分发的底层回调接口。根据 X3 文档与源码，可关注以下能力：

**注意事项（Since / Threading / Lifecycle）**

- **Since**：X3 增补垂直滑动、双指与轴向过滤能力。
- **Threading**：回调通常处于输入事件处理链路，建议保持轻量并尽快返回。
- **Lifecycle**：若在自定义组件中自行持有回调对象，需在组件销毁时解除关联，避免继续接收事件。

- 新增垂直滑动：`onTPSlideUpwards`、`onTPSlideDownwards`
- 新增双指事件：`onTPDoubleFingerClick`、`onTPDoubleFingerLongClick`
- `onTPSlideContinuous(delta, longClick, vertical)` 增加 `vertical` 参数
- `filterMode`（`NoFilter` / `OnlyX` / `OnlyY`）仅 X3 生效，可过滤连续滑动轴向数据

> 对于本文档覆盖的 app 调用面，这些能力最终会映射到 `TempleAction` 对应子类型，由 `TempleActionViewModel.state` 统一消费。

---

### 5.10 TempleActionViewModel

**包**：`com.ffalcon.mercury.android.sdk.touch`

用于在 Activity 中接收并分发镜腿手势的 ViewModel。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：配合 `BaseEventActivity` 提供手势事件流分发。
- **Threading**：内部使用 `viewModelScope` 与 Flow；订阅侧按需切换线程，UI 订阅建议主线程。
- **Lifecycle**：ViewModel 生命周期随宿主组件；建议在 `RESUMED` 或 `STARTED` 区间收集 `state`。

| 成员                 | 类型                         | 说明                                         |
|--------------------|----------------------------|--------------------------------------------|
| `userTempleAction` | `Channel<TempleAction>`    | 发送手势的 Channel（一般由 BaseEventActivity 内部发送）。 |
| `state`            | `SharedFlow<TempleAction>` | 订阅此 Flow 以处理手势。                            |

**示例**

```kotlin
lifecycleScope.launch {
    repeatOnLifecycle(Lifecycle.State.RESUMED) {
        templeActionViewModel.state.collect { action ->
            when (action) {
                is TempleAction.DoubleClick -> finish()
                is TempleAction.Click -> showDialog()
                else -> Unit
            }
        }
    }
}
```

---

### 5.11 actionName（MotionEvent 扩展）

**包**：`com.ffalcon.mercury.android.sdk.ui.activity`（在 `BaseTouchActivity` 同文件）

```kotlin
fun MotionEvent.actionName(): String
```

将 `MotionEvent.action` 转为可读字符串（如 `"ACTION_DOWN"`、`"ACTION_UP"`），便于日志或调试。

---

## 6. 对话框与 Toast

### 6.1 FToast

**包**：`com.ffalcon.mercury.android.sdk.ui.toast`

支持双目合目与 3D 效果的 Toast。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：SDK 通用合目提示组件。
- **Threading**：`show()` 建议在主线程调用。
- **Lifecycle**：依赖 `MercurySDK.mApplication`，必须先完成 `MercurySDK.init()`；Toast 为短生命周期瞬时 UI，不应承载关键业务流程。

| 方法                                                                  | 说明                            |
|---------------------------------------------------------------------|-------------------------------|
| `show(msg: String, short: Boolean = true, yOffset: Int = 200.dp)`   | 显示文本 Toast。                   |
| `show(msgResId: Int, short: Boolean = true, yOffset: Int = 200.dp)` | 使用字符串资源 ID。                   |
| `showCustom(msg, short, yOffset, bindingClz, initViewBlock)`        | 使用自定义 ViewBinding 布局与 3D 初始化。 |

**示例**

```kotlin
FToast.show("Click Confirm")
FToast.show(R.string.message, short = false, yOffset = 300.dp)
```

---

### 6.2 FDialog

**包**：`com.ffalcon.mercury.android.sdk.ui.dialog`

支持双目合目、镜腿触控和焦点切换的 Dialog。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：SDK 通用合目弹窗组件。
- **Threading**：Builder 配置、`show()/dismiss()` 及视图更新均应在主线程。
- **Lifecycle**：`dismiss()` 会取消内部协程作用域；建议在宿主 `onDestroy` 前确保弹窗关闭并释放相关资源。

**构造 / 使用方式**：通过 `FDialog.Builder<T : ViewBinding>` 链式配置。

| Builder 方法                                                          | 说明                                                 |
|---------------------------------------------------------------------|----------------------------------------------------|
| `setContentView(bindingClz, initViewBlock, params)`                 | 设置内容布局（ViewBinding 类）、初始化 block 和可选 LayoutParams。  |
| `setFocusTracker(focusTracker: FocusTracker)`                       | 设置对话框内焦点切换逻辑。                                      |
| `setCancelable(cancelable: Boolean)`                                | 是否可通过返回键取消。                                        |
| `setCanceledOnTouchOutside(cancel: Boolean)`                        | 是否点击外部取消。                                          |
| `setOnDismissListener(onDismiss)`                                   | 关闭回调。                                              |
| `setOnShowListener(onShow)`                                         | 显示回调。                                              |
| `setEventHandler(handler: (TempleAction, DialogInterface) -> Unit)` | 全局手势处理（如双击关闭）；按钮级事件在 FocusTracker 的 TrackInfo 中处理。 |
| `build(): FDialog`                                                  | 构建 FDialog。                                        |

**Builder 属性**（在 `setContentView` 之后可用）：

- `mPair: BindingPair<T>`：对话框内容的左右 Binding。
- `mFocusTracker: FocusTracker?`：已设置的焦点跟踪器。

**示例**

```kotlin
FDialog.Builder<DialogTestBinding>(this)
    .setCancelable(true)
    .setCanceledOnTouchOutside(true)
    .setContentView(DialogTestBinding::class.java) { pair, dialog ->
        // 初始化 pair.left / pair.right
    }
    .apply {
        val tracker = FocusTracker(true).apply {
            addFocusTarget(btnOkTrackInfo, btnCancelTrackInfo)
        }
        tracker.currentFocus(pair.left.btnCancel)
        setFocusTracker(tracker)
    }
    .setEventHandler { action, dialog ->
        if (action is TempleAction.DoubleClick) dialog.dismiss()
    }
    .build()
    .show()
```

---

### 6.3 FocusTracker（Dialog 用）

**包**：`com.ffalcon.mercury.android.sdk.ui.dialog`

与 `FocusHolder` 类似，但用于 FDialog 内部：管理 `TrackInfo` 列表，支持 `next()` / `previous()` 和循环。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：Dialog 焦点系统专用管理类。
- **Threading**：焦点切换与 UI 反馈应在主线程执行。
- **Lifecycle**：应与单个 Dialog 实例同生命周期使用，不建议跨 Dialog 复用同一实例。

| 构造                            | 说明                    |
|-------------------------------|-----------------------|
| `FocusTracker(loop: Boolean)` | `loop == true` 时焦点循环。 |

| 方法                                                | 说明         |
|---------------------------------------------------|------------|
| `addFocusTarget(vararg trackInfoList: TrackInfo)` | 添加可切换焦点的项。 |
| `currentFocus(target: Any)`                       | 设置当前焦点项。   |
| `next()` / `previous()`                           | 切换焦点。      |

---

### 6.4 TrackInfo

**包**：`com.ffalcon.mercury.android.sdk.ui.dialog`

FDialog 内单个可聚焦项的配置。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：Dialog 焦点项模型。
- **Threading**：`eventHandler` 中涉及 UI 或 dismiss 操作时应保证主线程。
- **Lifecycle**：`target` 建议绑定 Dialog 当前内容视图；Dialog 销毁后该配置应视为失效。

**构造**

```kotlin
TrackInfo(
    target: Any,
    eventHandler: (action: TempleAction, dialog: DialogInterface) -> Unit,
    focusChangeHandler: (hasFocus: Boolean) -> Unit,
    isSelected: Boolean = false
)
```

- `eventHandler`：该控件收到镜腿手势时的处理（如 Click 确认/取消并 dismiss）。
- `focusChangeHandler`：获得/失去焦点时更新 UI（需在内部用 `pair.updateView` + `make3DEffectForSide` 同步左右）。

---

## 7. RecyclerView 支持

### 7.1 RecyclerViewFocusTracker

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

基于**连续滑动距离**的 RecyclerView 焦点与滚动跟踪：根据 `SlideContinuous` 的 `delta` 切换当前选中项并滚动，适合“焦点跟随滑动”的列表。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于移动焦点项列表的核心跟踪器。
- **Threading**：所有 RecyclerView 与 Adapter 操作都必须在主线程。
- **Lifecycle**：建议在页面可见期间处理 `handleActionEvent()`；页面不可见或失焦时将 `focusObj.hasFocus` 置为 `false`。

**构造**

```kotlin
RecyclerViewFocusTracker(
    mPair: ViewPair<RecyclerView>,
    ignoreDelta: Int = IGNORE_DELTA,
    loop: Boolean = false
)
```

| 成员                      | 类型                              | 说明                                   |
|-------------------------|---------------------------------|--------------------------------------|
| `mPair`                 | `ViewPair<RecyclerView>`        | 左右两个 RecyclerView。                   |
| `focusObj`              | `IFocusable`                    | 是否拥有焦点（如整页获得焦点时列表才响应）。               |
| `currentSelectPos`      | `Int`                           | 当前选中项位置（可写通过 `setCurrentSelectPos`）。 |
| `onItemFocusListener`   | `OnItemFocusListener?`          | 选中项变化回调。                             |
| `onFocusChangeListener` | `OnTrackerFocusChangeListener?` | 焦点获得/失去回调。                           |
| `refreshListener`       | `PullToRefreshListener?`        | 在首项继续向前滑时触发“下拉刷新”等。                  |

**方法**

| 方法                                                                   | 说明                                                      |
|----------------------------------------------------------------------|---------------------------------------------------------|
| `setCurrentSelectPos(index: Int)`                                    | 设置当前选中索引。                                               |
| `handleActionEvent(it: TempleAction, block: (TempleAction) -> Unit)` | 有焦点时处理手势；连续滑动切换项，Click/DoubleClick 等转给 `block`。         |
| `checkedSelectPos(): Int`                                            | 有焦点时返回当前选中位置，否则 -1。                                     |
| `checkPosSelected(pos: Int): Boolean`                                | 某位置是否为当前选中且拥有焦点。                                        |
| `notifyDataSetChanged()`                                             | 左右 RecyclerView 的 adapter 各调用 `notifyDataSetChanged()`。 |

**示例**

```kotlin
favoriteTracker = RecyclerViewFocusTracker(
    ViewPair(mBindingPair.left.recyclerView, mBindingPair.right.recyclerView),
    ignoreDelta = 70
)
favoriteTracker.focusObj.hasFocus = true
// 在 state.collect 中：
favoriteTracker.handleActionEvent(it) { action ->
    when (action) {
        is TempleAction.Click -> { /* 点击当前项 */ }
        is TempleAction.DoubleClick -> finish()
        else -> {}
    }
}
```

---

### 7.2 RecyclerViewSlidingTracker

**包**：`com.ffalcon.mercury.android.sdk.ui.util`

基于**滑动吸附**的 RecyclerView 跟踪：依赖 `SnapHelper` 在滚动停止时确定当前选中项，并可订阅原始触摸事件实现“跟手”滚动。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于固定焦点项 + 跟手滚动场景。
- **Threading**：事件转换、`dispatchTouchEvent`、Adapter 刷新需在主线程。
- **Lifecycle**：注册原始事件流后应随页面焦点状态自动启停；离开页面前确保不再接收并转发事件。

**构造**

```kotlin
RecyclerViewSlidingTracker(mPair: ViewPair<RecyclerView>)
```

**方法**

| 方法                                                           | 说明                                                                         |
|--------------------------------------------------------------|----------------------------------------------------------------------------|
| `observeOriginMotionEventStream(dispatcher, eventTransform)` | 注册到 `MotionEventDispatcher`，将镜腿事件转换为 RecyclerView 的触摸事件，实现跟手。              |
| `setCurrentSelectPos(index: Int)`                            | 设置当前选中索引。                                                                  |
| `handleActionEvent(it, block)`                               | 处理手势；Click/DoubleClick 转给 `block`，SlideBackward 在首项时可触发 `refreshListener`。 |
| `smoothScrollToPosition(smoothScroll)`                       | 滚动到 `currentSelectPos`。                                                    |
| `notifyItemChanged(pos)` / `notifyDataSetChanged()`          | 刷新指定项或全量。                                                                  |

**注意**：需要为 RecyclerView 设置 `SnapHelper`（如 `StartSnapHelper`），并通过 `setTag(R.id.tag_snap_helper, snapHelper)` 设置，以便 `findSelectedPosition` 正确工作。SDK 中 `R.id.tag_snap_helper` 定义在 MercurySDK 的 `res/values/id.xml`。

**示例**

```kotlin
favoriteTracker = RecyclerViewSlidingTracker(
    ViewPair(mBindingPair.left.recyclerView, mBindingPair.right.recyclerView)
)
favoriteTracker.observeOriginMotionEventStream(motionEventDispatcher) { event ->
    MotionEvent.obtain(
        event.downTime, event.eventTime, event.action,
        320f, event.x, event.metaState
    )
}
recyclerView.apply {
    val snapHelper = StartSnapHelper(41.dp)
    snapHelper.attachToRecyclerView(this)
    setTag(com.ffalcon.mercury.android.sdk.R.id.tag_snap_helper, snapHelper)
}
```

---

### 7.3 StartSnapHelper

**包**：`com.ffalcon.mercury.android.sdk.util`

继承 `LinearSnapHelper`，使 RecyclerView 吸附到**起始方向**的第一个可见项（或带偏移），便于“固定焦点项”的列表使用。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：用于眼镜端列表起始吸附场景。
- **Threading**：作为 RecyclerView UI 组件，仅主线程使用。
- **Lifecycle**：建议与 RecyclerView 同步创建与销毁；更换 Adapter/LayoutManager 后可按需重新附着。

**构造**

```kotlin
StartSnapHelper(offset2Start: Int)
```

- `offset2Start`：相对起始边的偏移（如 41.dp），用于将吸附点对齐到首项中心等。

**使用**：`attachToRecyclerView(recyclerView)`，并与 `RecyclerViewSlidingTracker` 配合时设置 `setTag(R.id.tag_snap_helper, snapHelper)`。

---

### 7.4 BaseBindingHolder<T : ViewBinding> / SimpleBindingAdapter<B : ViewBinding>

**包**：`com.ffalcon.mercury.android.sdk.ext`

- **BaseBindingHolder**：持有一个 ViewBinding 的 `RecyclerView.ViewHolder`，通过 `binding` 访问布局。
- **SimpleBindingAdapter**：基于泛型 ViewBinding 的 Adapter，`onCreateViewHolder` 中通过反射 inflate 对应 Binding，返回 `BaseBindingHolder<B>`。

**注意事项（Since / Threading / Lifecycle）**

- **Since**：RecyclerView + ViewBinding 的基础封装。
- **Threading**：Adapter 生命周期回调运行在主线程；避免在 `onBindViewHolder` 执行重计算。
- **Lifecycle**：反射 inflate 依赖泛型签名，混淆/重构时应保持泛型结构正确。

列表项需左右同步时，可在 Adapter 内根据 `BindingPair.checkIsLeft` 或 tracker 的 `checkPosSelected` 更新 3D 与选中态。

---

## 8. 扩展与工具

### 8.1 dp / sp（扩展属性）

**包**：`com.ffalcon.mercury.android.sdk.ext`

将数值转为与系统密度无关的 px（基于 `Resources.getSystem().displayMetrics`）。

```kotlin
val Int.dp: Int
val Float.dp: Float
val Int.sp: Int
val Float.sp: Float
```

**示例**：`200.dp`、`41.dp`、`15f.dp`。

---

### 8.2 setViewVisible

**包**：`com.ffalcon.mercury.android.sdk.ext`

```kotlin
fun setViewVisible(visible: Boolean, vararg views: View)
```

将多个 View 统一设为 `View.VISIBLE` 或 `View.GONE`。

---

### 8.3 FLogger

**包**：`com.ffalcon.mercury.android.sdk.util`

统一 TAG 为 `"MercurySDK"` 的日志工具

**注意事项（Since / Threading / Lifecycle）**

- **Since**：SDK 内建统一日志门面。
- **Threading**：接口可在任意线程调用；长日志会分段输出。
- **Lifecycle**：`isDebug` 可在运行期更新，建议在调试切换动作后调用 `updateLogSwitch()` 同步状态。

| 方法                                     | 说明                                                  |
|----------------------------------------|-----------------------------------------------------|
| `updateLogSwitch()`                    | 根据 BuildConfig 与 `log.tag.MercurySDK` 更新 `isDebug`。 |
| `v(tag, msg)` / `v(msg)`               | Verbose。                                            |
| `d(tag, msg)` / `d(msg)`               | Debug。                                              |
| `i(tag, msg)` / `i(msg)`               | Info。                                               |
| `w(tag, msg)` / `w(msg)`               | Warn。                                               |
| `e(tag, msg, t?)` / `e(msg)` / `e(t?)` | Error。                                              |
| `printStack()`                         | 打印当前调用栈。                                            |

日志内容会附带调用位置（类名、方法名、文件名、行号），便于在 Logcat 中点击跳转。

---

### 8.4 开发调试建议（来自 RayNeo X3 ARSDK 文档）

- 建议主题 `windowBackground` 设为纯黑（`#FF000000`），以获得更自然的透视效果。
- 传感器、Camera、GPS 等高频能力在 `onPause`/`onDestroy` 及时释放，避免后台耗电与资源占用。
- 若需单目投屏调试，可参考 SDK 文档 的 `scrcpy --crop` 方案。

---

## 9. 全局约束总表（Checklist）

本节将前文各类 `Since / Threading / Lifecycle` 约束抽象为发布前检查清单，便于评审、测试与对外发布。

### 9.1 Threading 约束 Checklist

| 检查项                      | 适用对象                                                                                                    | 必须满足                                                                         |
|--------------------------|---------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| SDK 初始化在主线程且仅调用一次        | `MercurySDK`                                                                                            | 在 `Application#onCreate` 调用 `init()`，避免运行期重复初始化。                             |
| 所有 View/Binding 更新在主线程执行 | `BindingPair`、`BaseMirrorActivity`、`MirrorContainerView`、`BaseMirrorContainerView`、`BaseMirrorFragment` | `updateView`、`setLeft`、`bindTo`、焦点样式更新等均在主线程。                                |
| 手势事件流消费与 UI 改动线程一致       | `TempleActionViewModel`、`TempleAction`、`CommonTouchCallback`                                            | 事件可跨线程传递，但消费后修改 UI 必须切回主线程。                                                  |
| RecyclerView 操作在主线程      | `RecyclerViewFocusTracker`、`RecyclerViewSlidingTracker`、`StartSnapHelper`、`SimpleBindingAdapter`        | 包含 `notifyDataSetChanged`、滚动、`dispatchTouchEvent` 转发、`attachToRecyclerView`。 |
| 弹窗与 Toast 仅在主线程调用        | `FDialog`、`FToast`                                                                                      | `show()` / `dismiss()` / Builder 配置与视图更新均在主线程。                               |
| 日志可跨线程，避免在热路径重日志         | `FLogger`                                                                                               | 高频路径按需降级日志量，避免影响输入响应。                                                        |

### 9.2 Lifecycle 约束 Checklist

| 检查项                  | 适用对象                                                           | 必须满足                                                |
|----------------------|----------------------------------------------------------------|-----------------------------------------------------|
| 事件收集绑定可见生命周期         | `TempleActionViewModel` 消费侧                                    | 使用 `repeatOnLifecycle(STARTED/RESUMED)`，页面不可见时停止消费。 |
| 连接状态监听可自动释放          | `MobileState`                                                  | 通过受控协程收集；确保 Flow 完成后触发内部 `ContentObserver` 反注册。     |
| 焦点状态随页面切换正确转移        | `IFocusable`、`FocusHolder`、`FixPosFocusTracker`、`FocusTracker` | 页面/弹窗切换时及时切换 `hasFocus`，避免“隐藏页面仍响应手势”。              |
| 动态焦点项在销毁前清理          | `addFocusView`、`FocusViewHandle`                               | 不再使用时调用 `clearFocusView()`，避免残留 View 与引用。           |
| 跟手事件流随焦点启停           | `RecyclerViewSlidingTracker`                                   | 离开页面或失焦时停止原始事件转发。                                   |
| 组件资源按 Android 生命周期释放 | 相机/传感器/GPS/播放器场景                                               | 在 `onPause` / `onStop` / `onDestroy` 做对应释放，避免耗电与泄露。 |

### 9.3 发布评审 Checklist

```markdown
##  发布评审 Checklist

### Threading
- [ ] SDK 初始化在主线程且仅调用一次（`Application#onCreate` 调用 `MercurySDK.init()`）
- [ ] 所有 View/Binding 更新在主线程执行（`updateView` / `setLeft` / `bindTo`）
- [ ] 手势事件流消费后的 UI 修改在主线程执行
- [ ] RecyclerView 相关操作在主线程（滚动、刷新、事件转发、SnapHelper 附着）
- [ ] 弹窗与 Toast 仅在主线程调用（`show` / `dismiss` / Builder 配置）
- [ ] 高频路径日志已降噪，避免影响输入响应

### Lifecycle
- [ ] 手势事件收集绑定可见生命周期（`repeatOnLifecycle(STARTED/RESUMED)`）
- [ ] `MobileState` 连接监听在页面离开后可自动释放（不悬挂无界收集）
- [ ] 焦点状态在页面/弹窗切换时正确转移（避免隐藏页面响应手势）
- [ ] 动态焦点项在销毁前清理（调用 `clearFocusView()`）
- [ ] 跟手事件流随焦点启停（离开页面或失焦后停止转发）
- [ ] 相机/传感器/GPS/播放器资源在 `onPause/onStop/onDestroy` 正确释放
```

---

