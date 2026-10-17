/** Proposed discussion contract v0.2. Types only; limits require durable server enforcement. */
export type FollowUpRound = 1 | 2 | 3;
export type DiscussionRound = 0 | FollowUpRound;

export interface DiscussionReference {
  runId: string;
  threadId: string;
  /** 0 = initial request. 1..3 = the boundary-wide follow-up slot for this run. */
  round: DiscussionRound;
}

export type DiscussionAnswer = {
  questionId: string;
  source: "system_default" | "saved_preference" | "developer_choice" | "research_context";
} & (
  | { optionId: string; text: null }
  | { optionId: null; text: string }
);

export type FollowUpContent = {
  message: string;
} & (
  | { clarificationId: null; answers: [] }
  | { clarificationId: string; answers: [DiscussionAnswer, ...DiscussionAnswer[]] }
);

export type FollowUpDiscussionContext = DiscussionReference & {
  round: FollowUpRound;
  inReplyToRequestId: string;
  followUp: FollowUpContent;
};

export type DiscussionContext =
  | (DiscussionReference & { round: 0; inReplyToRequestId: null; followUp: null })
  | FollowUpDiscussionContext;

export type ClarificationQuestion = {
  questionId: string;
  prompt: string;
  /** Optional questions offer at least two choices. Required questions may allow free-text only. */
  options: Array<{ optionId: string; label: string }>;
} & (
  | { required: false; defaultOptionId: string }
  | { required: true; defaultOptionId: null }
);

/** Main -> MAS follow-up command; the full start/status/blueprint API remains to be designed. */
export interface MainFollowUpRequest {
  contractVersion: "0.2";
  /** Also the command's idempotency key; duplicate IDs must have identical content. */
  requestId: string;
  projectId: string;
  expectedProjectRevision: number;
  discussion: FollowUpDiscussionContext;
}
