package com.cagan.voiceclient;

import android.Manifest;
import android.animation.ObjectAnimator;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.View;
import android.widget.ImageButton;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private static final String SERVER_BASE_URL = "http://192.168.1.180:8000";
    private static final String CHAT_ENDPOINT = SERVER_BASE_URL + "/api/chat";
    private static final String PREFS = "voice_prefs";
    private static final String KEY_SESSION = "session_id";
    private static final int REQ_RECORD_AUDIO = 1001;

    private TextView statusText;
    private TextView userText;
    private ImageButton micButton;
    private View micHalo;

    private SpeechRecognizer speechRecognizer;
    private Intent speechIntent;
    private MediaPlayer mediaPlayer;
    private ObjectAnimator micIdleAnimator;

    private boolean busy = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusText = findViewById(R.id.statusText);
        userText = findViewById(R.id.userText);
        micButton = findViewById(R.id.micButton);
        micHalo = findViewById(R.id.micHalo);

        startIdleMicAnimation();
        setupSpeech();
        requestAudioPermissionIfNeeded();
        setIdleState(getString(R.string.status_ready));

        micButton.setOnClickListener(v -> {
            if (busy) {
                return;
            }
            startListening();
        });
    }

    private void startIdleMicAnimation() {
        if (micHalo == null) {
            return;
        }
        micIdleAnimator = ObjectAnimator.ofFloat(micHalo, "alpha", 0.45f, 0.9f, 0.45f);
        micIdleAnimator.setDuration(1600);
        micIdleAnimator.setRepeatCount(ObjectAnimator.INFINITE);
        micIdleAnimator.start();
    }

    private void setupSpeech() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            setIdleState(getString(R.string.status_no_speech));
            micButton.setEnabled(false);
            return;
        }

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
        speechRecognizer.setRecognitionListener(new RecognitionListener() {
            @Override
            public void onReadyForSpeech(Bundle params) {
                setStatus(getString(R.string.status_listening));
            }

            @Override
            public void onBeginningOfSpeech() {}

            @Override
            public void onRmsChanged(float rmsdB) {}

            @Override
            public void onBufferReceived(byte[] buffer) {}

            @Override
            public void onEndOfSpeech() {
                setStatus(getString(R.string.status_thinking));
            }

            @Override
            public void onError(int error) {
                setIdleState(getString(R.string.status_listen_error));
            }

            @Override
            public void onResults(Bundle results) {
                ArrayList<String> list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list == null || list.isEmpty()) {
                    setIdleState(getString(R.string.status_not_understood));
                    return;
                }
                String text = list.get(0) == null ? "" : list.get(0).trim();
                if (text.isEmpty()) {
                    setIdleState(getString(R.string.status_not_understood));
                    return;
                }
                userText.setText(getString(R.string.you_prefix) + text);
                sendToServer(text);
            }

            @Override
            public void onPartialResults(Bundle partialResults) {}

            @Override
            public void onEvent(int eventType, Bundle params) {}
        });

        speechIntent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        speechIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        speechIntent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR");
        speechIntent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        speechIntent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false);
    }

    private void startListening() {
        if (speechRecognizer == null) {
            setIdleState(getString(R.string.status_speech_not_ready));
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
            && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
            return;
        }
        busy = true;
        micButton.setVisibility(View.INVISIBLE);
        setStatus(getString(R.string.status_listening));
        speechRecognizer.startListening(speechIntent);
    }

    private void sendToServer(String text) {
        busy = true;
        micButton.setVisibility(View.INVISIBLE);
        setStatus(getString(R.string.status_thinking));

        new Thread(() -> {
            try {
                JSONObject payload = new JSONObject();
                payload.put("text", text);
                String session = getSessionId();
                if (!session.isEmpty()) {
                    payload.put("session_id", session);
                }
                payload.put("detected_lang", "tr");

                HttpURLConnection connection = (HttpURLConnection) new URL(CHAT_ENDPOINT).openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(15000);
                connection.setReadTimeout(120000);
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");

                try (OutputStream os = connection.getOutputStream()) {
                    os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                }

                int status = connection.getResponseCode();
                InputStream stream = status >= 200 && status < 300
                    ? connection.getInputStream()
                    : connection.getErrorStream();

                String body = readAll(stream);
                if (status < 200 || status >= 300) {
                    throw new RuntimeException(extractError(body, status));
                }

                JSONObject response = new JSONObject(body);
                String sessionId = response.optString("session_id", "");
                String reply = response.optString("reply_text", "Cevap bos.");
                String audioUrl = response.optString("audio_url", "");

                if (!sessionId.isEmpty()) {
                    saveSessionId(sessionId);
                }

                runOnUiThread(() -> {
                    setStatus(getString(R.string.bot_prefix) + reply);
                    playReplyAudio(audioUrl);
                });
            } catch (Exception exc) {
                runOnUiThread(() -> setIdleState(getString(R.string.status_error_prefix) + shortMsg(exc.getMessage())));
            }
        }).start();
    }

    private void playReplyAudio(String audioPath) {
        String path = audioPath == null ? "" : audioPath.trim();
        if (path.isEmpty()) {
            setIdleState(getString(R.string.status_ready_short));
            return;
        }
        String fullUrl = path.startsWith("http") ? path : SERVER_BASE_URL + path;

        releaseMediaPlayer();
        mediaPlayer = new MediaPlayer();
        mediaPlayer.setAudioAttributes(new AudioAttributes.Builder()
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .build());
        try {
            mediaPlayer.setDataSource(fullUrl);
            mediaPlayer.setOnPreparedListener(MediaPlayer::start);
            mediaPlayer.setOnCompletionListener(mp -> {
                releaseMediaPlayer();
                setIdleState(getString(R.string.status_ready_short));
            });
            mediaPlayer.setOnErrorListener((mp, what, extra) -> {
                releaseMediaPlayer();
                setIdleState(getString(R.string.status_audio_error));
                return true;
            });
            mediaPlayer.prepareAsync();
        } catch (Exception exc) {
            setIdleState(getString(R.string.status_audio_open_error));
        }
    }

    private void setStatus(String text) {
        statusText.setText(text);
    }

    private void setIdleState(String text) {
        busy = false;
        micButton.setVisibility(View.VISIBLE);
        micButton.setEnabled(true);
        setStatus(text);
    }

    private String getSessionId() {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        return sp.getString(KEY_SESSION, "");
    }

    private void saveSessionId(String sessionId) {
        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        sp.edit().putString(KEY_SESSION, sessionId).apply();
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int n;
            while ((n = in.read(buffer)) > 0) {
                out.write(buffer, 0, n);
            }
            return out.toString(StandardCharsets.UTF_8);
        }
    }

    private String extractError(String body, int code) {
        try {
            JSONObject j = new JSONObject(body);
            String msg = j.optString("error");
            if (!msg.isEmpty()) {
                return msg;
            }
        } catch (Exception ignored) {
        }
        return "HTTP " + code;
    }

    private String shortMsg(String msg) {
        if (msg == null || msg.isEmpty()) {
            return "bilinmeyen hata";
        }
        String oneLine = msg.replace('\n', ' ').replace('\r', ' ');
        if (oneLine.length() <= 120) {
            return oneLine;
        }
        return oneLine.substring(0, 120) + "...";
    }

    private void requestAudioPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, REQ_RECORD_AUDIO);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_RECORD_AUDIO) {
            if (grantResults.length == 0 || grantResults[0] != PackageManager.PERMISSION_GRANTED) {
                setIdleState(getString(R.string.status_mic_permission));
            }
        }
    }

    private void releaseMediaPlayer() {
        if (mediaPlayer == null) {
            return;
        }
        try {
            mediaPlayer.stop();
        } catch (Exception ignored) {
        }
        try {
            mediaPlayer.reset();
            mediaPlayer.release();
        } catch (Exception ignored) {
        }
        mediaPlayer = null;
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        releaseMediaPlayer();
        if (speechRecognizer != null) {
            try {
                speechRecognizer.stopListening();
                speechRecognizer.cancel();
                speechRecognizer.destroy();
            } catch (Exception ignored) {
            }
            speechRecognizer = null;
        }
        if (micIdleAnimator != null) {
            micIdleAnimator.cancel();
            micIdleAnimator = null;
        }
    }
}
