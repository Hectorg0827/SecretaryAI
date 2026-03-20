import React, { useRef, useEffect, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Speech from 'expo-speech';
import { useChatStore } from '../stores/chatStore';
import MessageBubble from '../components/chat/MessageBubble';
import ChatInput from '../components/chat/ChatInput';
import { COLORS, RADIUS } from '../theme';

export default function ChatScreen() {
  const { messages, isStreaming, error, sendMessage, newConversation, clearError } = useChatStore();
  const listRef = useRef<FlatList>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [messages]);

  const handleSend = async (text: string) => {
    if (!text.trim() || isStreaming) return;
    await sendMessage(text.trim());
  };

  const handleSpeak = async (text: string) => {
    if (isSpeaking) {
      Speech.stop();
      setIsSpeaking(false);
      return;
    }
    setIsSpeaking(true);
    Speech.speak(text, {
      language: 'en-US',
      onDone: () => setIsSpeaking(false),
      onError: () => setIsSpeaking(false),
    });
  };

  const lastAssistantMessage = [...messages].reverse().find((m) => m.role === 'assistant' && !m.isStreaming);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={0}
      >
        {/* Header */}
        <View style={styles.header}>
          <View style={styles.headerLeft}>
            <View style={styles.aiIcon}>
              <Text style={styles.aiIconText}>AI</Text>
            </View>
            <View>
              <Text style={styles.headerTitle}>Secretary AI</Text>
              <Text style={styles.headerStatus}>
                {isStreaming ? 'Thinking…' : 'Ready'}
              </Text>
            </View>
          </View>
          <View style={styles.headerActions}>
            {lastAssistantMessage ? (
              <TouchableOpacity
                style={styles.iconBtn}
                onPress={() => handleSpeak(lastAssistantMessage.content)}
              >
                <Ionicons
                  name={isSpeaking ? 'volume-mute' : 'volume-high'}
                  size={20}
                  color={COLORS.primary}
                />
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity style={styles.iconBtn} onPress={newConversation}>
              <Ionicons name="add-circle-outline" size={22} color={COLORS.textSecondary} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Message list */}
        {messages.length === 0 ? (
          <View style={styles.emptyState}>
            <View style={styles.emptyIcon}>
              <Ionicons name="chatbubbles" size={36} color={COLORS.primary} />
            </View>
            <Text style={styles.emptyTitle}>Ask me anything</Text>
            <Text style={styles.emptySubtitle}>
              Check inventory, review accounts, get sales reports, or compose emails — just ask.
            </Text>
            <View style={styles.suggestions}>
              {[
                'What\'s our inventory status?',
                'Show me at-risk accounts',
                'How are sales this month?',
                'Any pending approvals?',
              ].map((s) => (
                <TouchableOpacity
                  key={s}
                  style={styles.suggestionChip}
                  onPress={() => handleSend(s)}
                >
                  <Text style={styles.suggestionText}>{s}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <MessageBubble message={item} />}
            contentContainerStyle={styles.messageList}
            onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          />
        )}

        {error ? (
          <View style={styles.errorBar}>
            <Text style={styles.errorText}>{error}</Text>
            <TouchableOpacity onPress={clearError}>
              <Ionicons name="close" size={16} color={COLORS.danger} />
            </TouchableOpacity>
          </View>
        ) : null}

        {/* Input */}
        <ChatInput onSend={handleSend} disabled={isStreaming} />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: COLORS.surface,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  aiIcon: {
    width: 36,
    height: 36,
    borderRadius: RADIUS.sm,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  aiIconText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  headerTitle: { fontSize: 15, fontWeight: '700', color: COLORS.text },
  headerStatus: { fontSize: 12, color: COLORS.textMuted },
  headerActions: { flexDirection: 'row', gap: 4 },
  iconBtn: { padding: 6 },
  messageList: { padding: 16, paddingBottom: 8 },
  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  emptyIcon: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 16,
  },
  emptyTitle: { fontSize: 20, fontWeight: '700', color: COLORS.text, marginBottom: 8 },
  emptySubtitle: {
    fontSize: 14,
    color: COLORS.textSecondary,
    textAlign: 'center',
    lineHeight: 20,
    marginBottom: 24,
  },
  suggestions: { gap: 8, width: '100%' },
  suggestionChip: {
    backgroundColor: COLORS.surface,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.lg,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  suggestionText: { color: COLORS.text, fontSize: 14 },
  errorBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: COLORS.dangerLight,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  errorText: { color: COLORS.danger, fontSize: 13, flex: 1 },
});
