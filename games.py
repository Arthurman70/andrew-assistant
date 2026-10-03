"""Deterministic voice games. Chess rules are supplied by python-chess."""
import random
import re
import time

TRIVIA=[('What planet is known as the red planet?','mars'),('How many sides does a hexagon have?','six'),
 ('What is the largest ocean?','pacific'),('Which animal is the tallest?','giraffe'),
 ('What gas do plants absorb from the air?','carbon dioxide'),('How many players are on a soccer team on the field?','eleven'),
 ('What is the capital of France?','paris'),('What instrument has eighty eight keys?','piano'),
 ('What is frozen water called?','ice'),('Which planet has prominent rings?','saturn'),
 ('What is the fastest land animal?','cheetah'),('How many colors are in a rainbow?','seven')]
WORDS={'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','eleven':'11'}

class Games:
    def __init__(self,app):self.app=app
    def state(self,source):return self.app.get('game_'+source) or {}
    def save(self,source,state):self.app.set('game_'+source,state)
    def start(self,kind,source):
        if kind=='chess':state={'kind':kind,'moves':[],'message':'You play white. Say “move e2 to e4”, or name a piece and square.'}
        elif kind=='tic tac toe':state={'kind':kind,'cells':['']*9,'message':'You are X. Choose a square from 1 to 9, top left to bottom right.'}
        elif kind=='trivia':
            order=random.sample(range(len(TRIVIA)),5);state={'kind':kind,'questions':order,'index':0,'score':0,'message':TRIVIA[order[0]][0]}
        elif kind=='guess the number':state={'kind':kind,'secret':random.randint(1,100),'tries':0,'message':'I picked a number from 1 to 100. What is your guess?'}
        else:raise ValueError('Choose chess, tic tac toe, trivia, or guess the number.')
        self.save(source,state);return state['message']
    def board(self,state):
        import chess
        board=chess.Board()
        for uci in state['moves']:board.push_uci(uci)
        return board
    def snapshot(self,source):
        state=dict(self.state(source));state.pop('secret',None);state.pop('questions',None)
        if state.get('kind')=='chess':
            board=self.board(state);state['fen']=board.fen();state['turn']='white' if board.turn else 'black'
            state['squares']=[{'square':__import__('chess').square_name(sq),'piece':board.piece_at(sq).symbol() if board.piece_at(sq) else ''} for sq in range(64)]
        return state
    def chess_move(self,state,text):
        import chess
        board=self.board(state)
        if board.is_game_over():return 'This game is over. Say “new chess game” to play again.'
        text=re.sub(r'^(?:move |play )','',text.lower()).strip()
        for word,num in WORDS.items():text=re.sub(r'\b'+word+r'\b',num,text)
        text=re.sub(r'\b([a-h])\s+([1-8])\b',r'\1\2',text)
        text=text.replace('takes','to').replace('captures','to')
        candidates=[]
        squares=re.findall(r'\b[a-h][1-8]\b',text)
        promotion=next((p for p in ('queen','rook','bishop','knight') if 'promote to '+p in text),'queen')
        if len(squares)==2:
            candidates=[m for m in board.legal_moves if chess.square_name(m.from_square)==squares[0] and chess.square_name(m.to_square)==squares[1]
                        and (not m.promotion or m.promotion==chess.PIECE_NAMES.index(promotion))]
        elif len(squares)==1:
            piece=next((p for p in chess.PIECE_NAMES[1:] if p in text),'pawn')
            candidates=[m for m in board.legal_moves if chess.square_name(m.to_square)==squares[0] and board.piece_at(m.from_square).piece_type==chess.PIECE_NAMES.index(piece)]
        elif 'castle' in text:
            candidates=[m for m in board.legal_moves if board.is_queenside_castling(m) if 'queen' in text] if 'queen' in text else [m for m in board.legal_moves if board.is_kingside_castling(m)]
        else:
            try:candidates=[board.parse_san(text.replace('0','O'))]
            except ValueError:pass
        if len(candidates)>1:return 'More than one piece can move there. Say the starting and ending squares.'
        if not candidates:return 'That is not a legal move. Try “move e2 to e4”, or ask for legal moves.'
        def describe(move):
            if board.is_castling(move):return 'castled '+('kingside' if board.is_kingside_castling(move) else 'queenside')
            return chess.PIECE_NAMES[board.piece_at(move.from_square).piece_type]+' to '+chess.square_name(move.to_square)
        chosen=candidates[0];said=describe(chosen);board.push(chosen);state['moves'].append(chosen.uci())
        message='You played '+said+'. '
        if not board.is_game_over():
            move=self.choose_chess(board);reply=describe(move);board.push(move);state['moves'].append(move.uci());message+='I played '+reply+'. '
        if board.is_checkmate():message+='Checkmate. '+('You win!' if board.turn==chess.BLACK else 'I win this round.')
        elif board.is_game_over():message+='Draw. '+board.result()
        elif board.is_check():message+='You are in check.'
        else:message+='Your move.'
        return message
    @staticmethod
    def choose_chess(board):
        import chess
        values={chess.PAWN:100,chess.KNIGHT:320,chess.BISHOP:330,chess.ROOK:500,chess.QUEEN:900,chess.KING:0}
        deadline=time.monotonic()+1.2
        def score():
            if board.is_checkmate():return -100000 if board.turn else 100000
            return sum(values[p.piece_type]*(1 if p.color else -1) for p in board.piece_map().values())
        def search(depth,alpha,beta):
            if depth==0 or board.is_game_over() or time.monotonic()>deadline:return score()
            maximize=board.turn;best=-1e9 if maximize else 1e9
            for move in sorted(board.legal_moves,key=lambda m:board.is_capture(m),reverse=True):
                board.push(move);value=search(depth-1,alpha,beta);board.pop()
                best=max(best,value) if maximize else min(best,value)
                if maximize:alpha=max(alpha,best)
                else:beta=min(beta,best)
                if beta<=alpha:break
            return best
        options=list(board.legal_moves);random.shuffle(options);best=options[0];value=1e9
        for move in options:
            board.push(move);v=search(2,-1e9,1e9);board.pop()
            if v<value:best,value=move,v
            if time.monotonic()>deadline:break
        return best
    @staticmethod
    def winner(cells):
        for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
            if cells[a] and cells[a]==cells[b]==cells[c]:return cells[a]
        return 'draw' if all(cells) else ''
    def ttt(self,state,text):
        if self.winner(state['cells']):return 'This round is over. Say “play tic tac toe” for another.'
        for word,num in WORDS.items():text=re.sub(r'\b'+word+r'\b',num,text)
        positions={'top left':1,'top middle':2,'top right':3,'middle left':4,'center':5,'middle':5,'middle right':6,'bottom left':7,'bottom middle':8,'bottom right':9}
        pos=next((n for k,n in positions.items() if text==k),None)
        match=re.fullmatch(r'(?:square |move |play )?([1-9])',text)
        if match:pos=int(match[1])
        if not pos:return 'Choose a square from one to nine.'
        cells=state['cells']
        if cells[pos-1]:return 'That square is taken. Choose another.'
        cells[pos-1]='X'
        def solve(turn):
            winner=self.winner(cells)
            if winner:return {'O':1,'X':-1,'draw':0}[winner]
            scores=[]
            for i,c in enumerate(cells):
                if not c:
                    cells[i]=turn;scores.append(solve('X' if turn=='O' else 'O'));cells[i]=''
            return (max if turn=='O' else min)(scores)
        chosen=None;best=-2
        if not self.winner(cells):
            for i in [4,0,2,6,8,1,3,5,7]:
                if not cells[i]:
                    cells[i]='O';score=solve('X');cells[i]=''
                    if score>best:best,chosen=score,i
            if chosen is not None:cells[chosen]='O'
        winner=self.winner(cells)
        return {'O':'I won this round!','X':'You won!','draw':'A draw. Well played.'}.get(winner,f'I chose square {(chosen or 0)+1}. Your turn.')
    def route(self,text,source):
        low=text.lower().strip();state=self.state(source)
        match=re.fullmatch(r'(?:let\'s |lets )?(?:play|start|new)(?: a| game of)? (chess|tic[ -]tac[ -]toe|trivia|guess the number)(?: game)?',low)
        if match:return self.start(re.sub(r'[-]',' ',match[1]),source)
        if low in ('games','what games can we play'):return 'We can play chess, tic tac toe, five question trivia, or guess the number. The game appears on this device.'
        if low in ('stop game','quit game','end game'):
            self.save(source,{});return 'Game closed.'
        if not state:return None
        if low in ('game status','show game','whose turn is it'):return state['message']
        if state['kind']=='chess':
            if low in ('undo move','take back','undo last move'):
                state['moves']=state['moves'][:-2];answer='Took back the last pair of moves. Your turn.'
            elif low in ('legal moves','what moves can i make'):
                board=self.board(state);return ', '.join(board.san(m) for m in list(board.legal_moves)[:35])
            elif low.startswith(('move ','play ','castle','pawn ','knight ','bishop ','rook ','queen ','king ')) or re.fullmatch(r'[a-h][1-8]\s*(?:to)?\s*[a-h][1-8]',low):answer=self.chess_move(state,low)
            else:return None
        elif state['kind']=='tic tac toe':
            if not re.fullmatch(r'(?:square |move |play )?(?:[1-9]|one|two|three|four|five|six|seven|eight|nine|(?:top|middle|bottom) (?:left|middle|right)|center|middle)',low):return None
            answer=self.ttt(state,low)
        elif state['kind']=='trivia':
            if state.get('done'):return None
            if low.startswith(('set ','turn ','remember ','what time','open ','switch ','use ')):return None
            correct=TRIVIA[state['questions'][state['index']]][1]
            normalized=' '.join(WORDS.get(w,w) for w in low.split());expected=WORDS.get(correct,correct)
            right=re.search(r'\b'+re.escape(expected)+r'\b',normalized) is not None
            state['score']+=int(right);state['index']+=1
            answer='Correct! ' if right else 'The answer was '+correct+'. '
            if state['index']==5:state['done']=True;answer+=f'You scored {state["score"]} out of five.'
            else:answer+=TRIVIA[state['questions'][state['index']]][0]
        else:
            match=re.fullmatch(r'(?:guess |is it )?(\d{1,3})',low)
            if not match:return None
            if state.get('done'):return 'You already solved it! Say “play guess the number” for another round.'
            if not 1<=int(match[1])<=100:return 'Choose a number from one to one hundred.'
            guess=int(match[1]);state['tries']+=1
            answer='Higher.' if guess<state['secret'] else 'Lower.' if guess>state['secret'] else f'You got it in {state["tries"]} guesses!'
            if guess==state['secret']:state['done']=True
        state['message']=answer;self.save(source,state);return answer
