package com.example.zrgfirstapp

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/** Adds the shared microphone to CameraX's silent video, without another mic input. */
internal object StudentVideoAudio {
    fun merge(video: File, wav: File, output: File) {
        val aac = File(output.parentFile, "audio.m4a")
        encode(wav, aac)
        val mux = MediaMuxer(output.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        val sources = mutableListOf<Pair<MediaExtractor, Int>>()
        try {
            for ((file, prefix) in listOf(video to "video/", aac to "audio/")) {
                val extractor = MediaExtractor()
                try {
                    extractor.setDataSource(file.path)
                    val track = (0 until extractor.trackCount).first { extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith(prefix) == true }
                    val format = extractor.getTrackFormat(track)
                    if (prefix == "video/" && format.containsKey(MediaFormat.KEY_ROTATION)) mux.setOrientationHint(format.getInteger(MediaFormat.KEY_ROTATION))
                    extractor.selectTrack(track)
                    sources += extractor to mux.addTrack(format)
                } catch (error: Exception) { extractor.release(); throw error }
            }
            mux.start()
            val info = MediaCodec.BufferInfo()
            val buffer = ByteBuffer.allocateDirect(2 * 1024 * 1024)
            for ((extractor, track) in sources) {
                val first = extractor.sampleTime.coerceAtLeast(0)
                while (true) {
                    buffer.clear()
                    val size = extractor.readSampleData(buffer, 0)
                    if (size < 0) break
                    info.set(0, size, (extractor.sampleTime - first).coerceAtLeast(0), extractor.sampleFlags)
                    mux.writeSampleData(track, buffer, info)
                    extractor.advance()
                }
            }
            mux.stop()
        } finally {
            sources.forEach { it.first.release() }
            mux.release()
        }
    }

    private fun encode(wav: File, output: File) {
        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
        val mux = MediaMuxer(output.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        try {
            val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, 16_000, 1).apply {
                setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
                setInteger(MediaFormat.KEY_BIT_RATE, 64_000)
            }
            codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.start()
            var track = -1
            var endInput = false
            var bytes = 0L
            val info = MediaCodec.BufferInfo()
            wav.inputStream().use { input ->
                check(input.skip(44) == 44L)
                val pcm = ByteArray(4096)
                while (true) {
                    if (!endInput) {
                        val index = codec.dequeueInputBuffer(10_000)
                        if (index >= 0) {
                            val buffer = codec.getInputBuffer(index)!!
                            buffer.clear()
                            val size = input.read(pcm, 0, minOf(pcm.size, buffer.remaining()))
                            val pts = bytes * 1_000_000L / 32_000L
                            if (size < 0) {
                                endInput = true
                                codec.queueInputBuffer(index, 0, 0, pts, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            } else {
                                buffer.put(pcm, 0, size)
                                codec.queueInputBuffer(index, 0, size, pts, 0)
                                bytes += size
                            }
                        }
                    }
                    val index = codec.dequeueOutputBuffer(info, 10_000)
                    if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        track = mux.addTrack(codec.outputFormat)
                        mux.start()
                    } else if (index >= 0) {
                        val buffer = codec.getOutputBuffer(index)!!
                        if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0 && info.size > 0) {
                            buffer.position(info.offset)
                            buffer.limit(info.offset + info.size)
                            mux.writeSampleData(track, buffer, info)
                        }
                        val done = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                        codec.releaseOutputBuffer(index, false)
                        if (done) break
                    }
                }
            }
            codec.stop()
            mux.stop()
        } finally { codec.release(); mux.release() }
    }
}
