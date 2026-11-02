import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Modal, View, Text, ScrollView, StyleSheet, TouchableOpacity, ActivityIndicator,
  type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { X } from 'lucide-react-native';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';
import { LEGAL_DOCS, isScrolledToEnd, loadLegalDoc, type LegalKey, type LoadedDoc } from '../lib/legal-terms';

type State = { status: 'loading' | 'ready' | 'error'; doc?: LegalKey; data?: LoadedDoc };

/**
 * A legal document as a full-height sheet. Reaching the end (within 24px)
 * marks it read; "I've read this" stays disabled until then.
 */
export function LegalDocSheet({ doc, read, onRead, onLoaded, onClose }: {
  doc: LegalKey | null;
  read: boolean;
  onRead: (k: LegalKey) => void;
  onLoaded?: (k: LegalKey, d: LoadedDoc) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<State>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const box = useRef({ contentHeight: 0, offsetY: 0, viewHeight: 0 });
  const meta = doc ? LEGAL_DOCS[doc] : null;

  useEffect(() => {
    if (!doc) return undefined;
    let live = true;
    box.current = { contentHeight: 0, offsetY: 0, viewHeight: 0 };
    setState({ status: 'loading', doc });
    loadLegalDoc(doc).then(
      (data) => { if (live) { setState({ status: 'ready', data, doc }); onLoaded?.(doc, data); } },
      () => { if (live) setState({ status: 'error', doc }); },
    );
    return () => { live = false; };
  }, [doc, attempt, onLoaded]);

  const check = useCallback(() => {
    if (!doc || state.status !== 'ready' || state.doc !== doc || read) return;
    if (isScrolledToEnd(box.current)) onRead(doc);
  }, [doc, read, onRead, state.status, state.doc]);

  // Sizes measured while "Loading…" was showing must not count as the end of the document.
  useEffect(() => {
    box.current = { contentHeight: 0, offsetY: 0, viewHeight: box.current.viewHeight };
  }, [state.status, state.doc]);

  useEffect(() => { check(); }, [check]);

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const n = e.nativeEvent;
    if (state.status !== 'ready' || state.doc !== doc) return;
    box.current = { contentHeight: n.contentSize.height, offsetY: n.contentOffset.y, viewHeight: n.layoutMeasurement.height };
    check();
  };
  const onLayout = (e: LayoutChangeEvent) => { box.current = { ...box.current, viewHeight: e.nativeEvent.layout.height }; check(); };
  const onContentSize = (_w: number, h: number) => {
    if (state.status !== 'ready' || state.doc !== doc) return;
    box.current = { ...box.current, contentHeight: h };
    check();
  };

  if (!doc || !meta) return null;
  const view: State = state.doc === doc ? state : { status: 'loading' };
  const paragraphs = view.status === 'ready' && view.data ? view.data.text.split(/\n{2,}/) : [];

  return (
    <Modal visible animationType="slide" presentationStyle="fullScreen" onRequestClose={onClose}>
      <SafeAreaView style={s.panel} edges={['top', 'bottom']} testID={`legal-sheet-${doc}`}>
        <View style={s.head}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.title} accessibilityRole="header">{meta.title}</Text>
            {view.data?.updated ? <Text style={s.updated}>{view.data.updated}</Text> : null}
          </View>
          <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Close" style={s.x} hitSlop={10}>
            <X size={18} color={TOKENS.moon} strokeWidth={1.5} />
          </TouchableOpacity>
        </View>
        <ScrollView
          style={s.body}
          contentContainerStyle={s.bodyInner}
          onScroll={onScroll}
          scrollEventThrottle={64}
          onLayout={onLayout}
          onContentSizeChange={onContentSize}
          accessibilityLabel={`${meta.title} text`}
          testID="legal-sheet-body"
        >
          {view.status === 'ready' ? (
            paragraphs.map((p, i) => <Text key={i} style={s.para} selectable>{p}</Text>)
          ) : view.status === 'error' ? (
            <View accessibilityRole="alert" style={s.msg}>
              <Text style={s.para}>This document didn't load.</Text>
              <TouchableOpacity onPress={() => setAttempt((n) => n + 1)} accessibilityRole="button">
                <Text style={[s.para, s.link]}>Try again</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <View style={s.msg}><ActivityIndicator color={TOKENS.moonMute} /><Text style={s.para}>Loading…</Text></View>
          )}
        </ScrollView>
        <View style={s.foot}>
          <Text style={[s.cue, read && s.cueDone]} testID="legal-sheet-cue">
            {read ? 'Read' : 'Scroll to the end to continue'}
          </Text>
          <TouchableOpacity
            style={[s.done, !read && s.disabled]}
            disabled={!read}
            onPress={onClose}
            accessibilityRole="button"
            accessibilityState={{ disabled: !read }}
            testID="legal-sheet-done"
          >
            <Text style={s.doneLabel}>I've read this</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const s = StyleSheet.create({
  panel: { flex: 1, backgroundColor: TOKENS.night },
  head: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingHorizontal: 20, paddingTop: 16, paddingBottom: 14, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  title: { fontFamily: TYPOGRAPHY.heading, fontSize: 18, color: TOKENS.moon, letterSpacing: -0.3 },
  updated: { fontFamily: TYPOGRAPHY.body, fontSize: 12, color: TOKENS.moonMute, marginTop: 4 },
  x: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center' },
  body: { flex: 1 },
  bodyInner: { paddingHorizontal: 20, paddingVertical: 18 },
  para: { fontFamily: TYPOGRAPHY.body, fontSize: 14, lineHeight: 22, color: TOKENS.moonSoft, marginBottom: 12 },
  link: { color: TOKENS.moon, textDecorationLine: 'underline' },
  msg: { paddingVertical: 24, gap: 8 },
  foot: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 20, paddingVertical: 12, borderTopWidth: 1, borderTopColor: TOKENS.nightHair },
  cue: { flex: 1, fontFamily: TYPOGRAPHY.body, fontSize: 13, color: TOKENS.moonMute },
  cueDone: { color: TOKENS.moon },
  done: { minHeight: 44, paddingHorizontal: 16, borderRadius: TOKENS.radius, borderWidth: 1, borderColor: TOKENS.nightHairS, alignItems: 'center', justifyContent: 'center' },
  doneLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 14, color: TOKENS.moon },
  disabled: { opacity: 0.32 },
});
