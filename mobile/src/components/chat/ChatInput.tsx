import React, { useState, useRef } from 'react';
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Platform,
  ActivityIndicator,
  Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Audio } from 'expo-av';
import { transcribeAudio } from '../../api/chat';
import { COLORS, RADIUS } from '../../theme';

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

type RecordingState = 'idle' | 'recording' | 'transcribing';

export default function ChatInput({ onSend, disabled }: Props) {
  const [text, setText] = useState('');
  const [recordingState, setRecordingState] = useState<RecordingState>('idle');
  const [recordingError, setRecordingError] = useState<string | null>(null);
  const recordingRef = useRef<Audio.Recording | null>(null);
  const inputRef = useRef<TextInput>(null);
  const pulseAnim = useRef(new Animated.Value(1)).current;

  const startPulse = () => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(pulseAnim, { toValue: 1.25, duration: 600, useNativeDriver: true }),
        Animated.timing(pulseAnim, { toValue: 1, duration: 600, useNativeDriver: true }),
      ]),
    ).start();
  };

  const stopPulse = () => {
    pulseAnim.stopAnimation();
    pulseAnim.setValue(1);
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    inputRef.current?.blur();
  };

  const startRecording = async () => {
    setRecordingError(null);
    try {
      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) {
        setRecordingError('Microphone permission denied');
        return;
      }
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });
      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
      );
      recordingRef.current = recording;
      setRecordingState('recording');
      startPulse();
    } catch (err) {
      setRecordingError('Could not start recording');
    }
  };

  const stopAndTranscribe = async () => {
    stopPulse();
    const recording = recordingRef.current;
    if (!recording) return;
    setRecordingState('transcribing');
    try {
      await recording.stopAndUnloadAsync();
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false });
      const uri = recording.getURI();
      recordingRef.current = null;
      if (!uri) throw new Error('No audio URI');
      const transcribed = await transcribeAudio(uri);
      if (transcribed.trim()) {
        setText((prev) => (prev ? `${prev} ${transcribed}` : transcribed));
      }
    } catch (err) {
      setRecordingError(err instanceof Error ? err.message : 'Transcription failed');
    } finally {
      setRecordingState('idle');
    }
  };

  const handleVoicePress = () => {
    if (recordingState === 'idle') startRecording();
    else if (recordingState === 'recording') stopAndTranscribe();
  };

  const canSend = text.trim().length > 0 && !disabled && recordingState === 'idle';
  const isRecording = recordingState === 'recording';
  const isTranscribing = recordingState === 'transcribing';

  return (
    <View style={styles.container}>
      {recordingError ? (
        <View style={styles.errorBar}>
          <Ionicons name="alert-circle" size={13} color={COLORS.danger} />
        </View>
      ) : null}
      <View style={[styles.inputRow, isRecording && styles.inputRowRecording]}>
        {/* Voice button */}
        <Animated.View style={{ transform: [{ scale: isRecording ? pulseAnim : 1 }] }}>
          <TouchableOpacity
            style={[
              styles.voiceBtn,
              isRecording && styles.voiceBtnActive,
              isTranscribing && styles.voiceBtnTranscribing,
            ]}
            onPress={handleVoicePress}
            disabled={disabled || isTranscribing}
          >
            {isTranscribing ? (
              <ActivityIndicator size="small" color={COLORS.primary} />
            ) : (
              <Ionicons
                name={isRecording ? 'stop' : 'mic'}
                size={18}
                color={isRecording ? '#fff' : COLORS.textSecondary}
              />
            )}
          </TouchableOpacity>
        </Animated.View>

        {/* Text input */}
        <TextInput
          ref={inputRef}
          style={styles.input}
          value={text}
          onChangeText={setText}
          placeholder={isRecording ? 'Listening…' : 'Ask Secretary AI…'}
          placeholderTextColor={isRecording ? COLORS.danger : COLORS.textMuted}
          multiline
          maxLength={2000}
          editable={!disabled && !isRecording}
          onSubmitEditing={Platform.OS === 'web' ? handleSend : undefined}
        />

        {/* Send button */}
        <TouchableOpacity
          style={[styles.sendBtn, canSend ? styles.sendBtnActive : styles.sendBtnInactive]}
          onPress={handleSend}
          disabled={!canSend}
          activeOpacity={0.8}
        >
          {disabled && recordingState === 'idle' ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <Ionicons name="arrow-up" size={18} color={canSend ? '#fff' : COLORS.textMuted} />
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: COLORS.surface,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingHorizontal: 12,
    paddingVertical: 10,
    paddingBottom: Platform.OS === 'ios' ? 16 : 10,
  },
  errorBar: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
    gap: 4,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.xl,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingLeft: 6,
    paddingRight: 6,
    paddingVertical: 6,
    gap: 4,
  },
  inputRowRecording: {
    borderColor: COLORS.danger,
    borderWidth: 1.5,
  },
  input: {
    flex: 1,
    fontSize: 15,
    color: COLORS.text,
    maxHeight: 120,
    paddingTop: 4,
    paddingBottom: 4,
    paddingHorizontal: 4,
  },
  voiceBtn: {
    width: 34,
    height: 34,
    borderRadius: 17,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.border,
    flexShrink: 0,
  },
  voiceBtnActive: {
    backgroundColor: COLORS.danger,
  },
  voiceBtnTranscribing: {
    backgroundColor: COLORS.primaryLight,
  },
  sendBtn: {
    width: 34,
    height: 34,
    borderRadius: 17,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  sendBtnActive: { backgroundColor: COLORS.primary },
  sendBtnInactive: { backgroundColor: COLORS.border },
});
