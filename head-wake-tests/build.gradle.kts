plugins {
    alias(libs.plugins.android.application)
}

android {
    namespace = "com.chemistry.headwaketest"
    compileSdk {
        version = release(36) { minorApiLevel = 1 }
    }
    defaultConfig {
        applicationId = "com.chemistry.headwaketest"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
    }
    flavorDimensions += "interaction"
    productFlavors {
        create("gesture") {
            dimension = "interaction"
            applicationIdSuffix = ".gesture"
            buildConfigField("boolean", "CURSOR_MODE", "false")
            resValue("string", "app_name", "头部动作测试")
        }
        create("cursor") {
            dimension = "interaction"
            applicationIdSuffix = ".cursor"
            buildConfigField("boolean", "CURSOR_MODE", "true")
            resValue("string", "app_name", "十字边沿测试")
        }
    }
    buildFeatures {
        buildConfig = true
        resValues = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}

dependencies { testImplementation(libs.junit) }
