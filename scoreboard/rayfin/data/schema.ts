import { LiveBoard } from './LiveBoard.js';
import { GameSession } from './GameSession.js';
import { UserSession } from './UserSession.js';
import { SpecialSession } from './SpecialSession.js';
import { GamePlayerSession } from './GamePlayerSession.js';

export type AppSchema = {
  LiveBoard: LiveBoard;
  GameSession: GameSession;
  UserSession: UserSession;
  SpecialSession: SpecialSession;
  GamePlayerSession: GamePlayerSession;
};

export const schema = [LiveBoard, GameSession, UserSession, SpecialSession, GamePlayerSession];
